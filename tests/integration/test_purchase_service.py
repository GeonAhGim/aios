"""13.4 통합테스트 — 실제 dev DB 대상."""
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseError, PurchaseService
from src.services.verification_service import VerificationService
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


async def _listed_listing(pool, seller, price=None):
    from decimal import Decimal

    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(
        seller, strategy_id, version, price or Decimal("50.00")
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

    result = await service.purchase(buyer, listing.listing_id)

    assert result.status == "PENDING_PAYMENT"


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

    result = await service.purchase(buyer, listing.listing_id, risk_warning_acknowledged=True)

    assert result.risk_warning == "위험등급 불일치 — 공격형 전략입니다"


async def test_price_paid_recorded_from_listing_price(service, pool):
    from decimal import Decimal

    seller = await create_test_user(pool)
    listing = await _listed_listing(pool, seller, price=Decimal("75.50"))
    buyer = await create_test_user(pool)

    result = await service.purchase(buyer, listing.listing_id)

    async with pool.acquire() as conn:
        price_paid = await conn.fetchval(
            "SELECT price_paid FROM strategy_purchases WHERE id = $1", result.purchase_id
        )
    assert price_paid == Decimal("75.50")
