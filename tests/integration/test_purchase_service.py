"""13.4 통합테스트 — 실제 dev DB 대상.

ADR-2026-08-29 §1 반영 — 구매는 이제 지갑 잔액을 즉시 차감하고 그 자리
에서 CONFIRMED로 확정된다(구 PENDING_PAYMENT 중간 상태 제거).
"""
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.purchase_service import (
    InsufficientWalletBalanceError,
    PurchaseError,
    PurchaseService,
)
from src.services.strategy_access_service import StrategyAccessService
from src.services.verification_service import VerificationService
from src.services.wallet_service import PLATFORM_HOUSE_USER_ID
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _fund_wallet(pool: asyncpg.Pool, user_id: UUID, amount: Decimal) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            user_id,
            amount,
        )


async def _wallet_balance(pool: asyncpg.Pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        )
    return balance if balance is not None else Decimal("0")


async def _create_strategy(pool, owner_user_id) -> tuple[str, str]:
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id, version):
    return True


_UNSET = object()


async def _listed_listing(pool, seller, price=_UNSET):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(
        seller, strategy_id, version, Decimal("50.00") if price is _UNSET else price
    )
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    result = await verification_service.decide(submitted.id, verifier, "APPROVE")
    return result


@pytest.fixture
def service(pool):
    return PurchaseService(pool)


async def test_purchase_succeeds_for_listed_strategy(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller)
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("50.00"))

    result = await service.purchase(buyer, listing.listing_id)

    assert result.status == "CONFIRMED"


async def test_purchase_rejects_self_trade(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller)

    with pytest.raises(PurchaseError):
        await service.purchase(seller, listing.listing_id)


async def test_purchase_rejects_non_listed_status(service, pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    draft_listing = await listing_service.create_listing(seller, strategy_id, version, None)
    buyer = await create_test_user(pool)

    with pytest.raises(PurchaseError):
        await service.purchase(buyer, draft_listing.id)


async def test_purchase_rejects_nonexistent_listing(service, pool):
    buyer = await create_test_user(pool)

    with pytest.raises(PurchaseError):
        await service.purchase(buyer, 999999999)


async def test_purchase_blocked_by_unacknowledged_risk_warning(pool):
    async def warn(buyer_user_id, strategy_id, version):
        return "위험등급 불일치 — 공격형 전략입니다"

    service = PurchaseService(pool, check_risk_warning=warn)
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller)
    buyer = await create_test_user(pool)

    with pytest.raises(PurchaseError):
        await service.purchase(buyer, listing.listing_id)


async def test_purchase_succeeds_with_acknowledged_risk_warning(pool):
    async def warn(buyer_user_id, strategy_id, version):
        return "위험등급 불일치 — 공격형 전략입니다"

    service = PurchaseService(pool, check_risk_warning=warn)
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller)
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("50.00"))

    result = await service.purchase(buyer, listing.listing_id, risk_warning_acknowledged=True)

    assert result.risk_warning == "위험등급 불일치 — 공격형 전략입니다"


async def test_price_paid_recorded_from_listing_price(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("75.50"))
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("75.50"))

    result = await service.purchase(buyer, listing.listing_id)

    async with pool.acquire() as conn:
        price_paid = await conn.fetchval(
            "SELECT price_paid FROM strategy_purchases WHERE id = $1", result.purchase_id
        )
    assert price_paid == Decimal("75.50")


async def test_purchase_computes_and_stores_commission(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("100.00"))
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("100.00"))

    result = await service.purchase(buyer, listing.listing_id)

    assert result.platform_commission_amount == Decimal("15.0000")
    assert result.seller_payout_amount == Decimal("85.0000")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT platform_commission_rate, platform_commission_amount, seller_payout_amount "
            "FROM strategy_purchases WHERE id = $1",
            result.purchase_id,
        )
    assert row["platform_commission_rate"] == Decimal("0.1500")
    assert row["platform_commission_amount"] == Decimal("15.00")
    assert row["seller_payout_amount"] == Decimal("85.00")


async def test_purchase_with_no_price_stores_no_commission(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=None)
    buyer = await create_test_user(pool)

    result = await service.purchase(buyer, listing.listing_id)

    assert result.status == "CONFIRMED"
    assert result.platform_commission_amount is None
    assert result.seller_payout_amount is None


async def test_purchase_rejects_insufficient_balance(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("50.00"))
    buyer = await create_test_user(pool)

    with pytest.raises(InsufficientWalletBalanceError):
        await service.purchase(buyer, listing.listing_id)

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy_purchases WHERE listing_id = $1", listing.listing_id
        )
    assert count == 0


async def test_purchase_debits_buyer_and_credits_seller_and_platform(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("100.00"))
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("100.00"))
    house_before = await _wallet_balance(pool, PLATFORM_HOUSE_USER_ID)

    await service.purchase(buyer, listing.listing_id)

    assert await _wallet_balance(pool, buyer) == Decimal("0.00")
    assert await _wallet_balance(pool, seller) == Decimal("85.00")
    assert await _wallet_balance(pool, PLATFORM_HOUSE_USER_ID) == house_before + Decimal("15.00")


async def test_purchase_opens_execution_access_immediately(service, pool):
    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("50.00"))
    buyer = await create_test_user(pool)
    await _fund_wallet(pool, buyer, Decimal("50.00"))
    access_service = StrategyAccessService(pool)
    strategy_id, version = await _strategy_ref(pool, listing)

    await service.purchase(buyer, listing.listing_id)

    assert await access_service.can_access(buyer, strategy_id, version) is True


async def _strategy_ref(pool, listing) -> tuple[str, str]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT strategy_id, strategy_version FROM strategy_listings WHERE id = $1",
            listing.listing_id,
        )
    return row["strategy_id"], row["strategy_version"]
