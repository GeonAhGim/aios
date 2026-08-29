"""18.2 통합테스트 — 실제 dev DB 대상."""
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.dispute_resolution_service import (
    DisputeResolutionError,
    DisputeResolutionService,
)
from src.services.dispute_service import DisputeService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.verification_service import VerificationService
from tests.integration.conftest import create_test_user

_PURCHASE_PRICE = Decimal("10")


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            user_id,
            amount,
        )


async def _wallet_balance(pool, user_id) -> Decimal:
    async with pool.acquire() as conn:
        balance = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        )
    return balance if balance is not None else Decimal("0")


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


@pytest.fixture
def service(pool):
    return DisputeResolutionService(pool)


async def _always_eligible(strategy_id, version):
    return True


async def _create_strategy(pool, owner_user_id):
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


async def _open_dispute(pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    purchase_service = PurchaseService(pool)
    dispute_service = DisputeService(pool)

    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(
        seller, strategy_id, version, _PURCHASE_PRICE
    )
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    await _fund_wallet(pool, buyer, _PURCHASE_PRICE)
    purchase = await purchase_service.purchase(buyer, approved.listing_id)
    dispute = await dispute_service.submit(buyer, purchase.purchase_id, "성과가 다릅니다")
    return dispute, approved.listing_id, seller, buyer


async def test_get_detail_joins_listing_and_purchase(service, pool):
    dispute, listing_id, seller, buyer = await _open_dispute(pool)

    detail = await service.get_detail(dispute.id)

    assert detail.listing_id == listing_id
    assert detail.seller_user_id == seller
    assert detail.buyer_user_id == buyer
    assert detail.status == "OPEN"


async def test_resolve_normal_risk_realization_keeps_listing_status(service, pool):
    dispute, listing_id, _, _ = await _open_dispute(pool)
    admin = await create_test_user(pool)

    result = await service.resolve(
        dispute.id, admin, "NORMAL_RISK_REALIZATION", "정상적인 시장 리스크로 판단"
    )

    assert result.listing_status == "LISTED"
    async with pool.acquire() as conn:
        listing_status = await conn.fetchval(
            "SELECT status FROM strategy_listings WHERE id = $1", listing_id
        )
    assert listing_status == "LISTED"


async def test_resolve_delisted_and_refund_delists_listing(service, pool):
    dispute, listing_id, _, _ = await _open_dispute(pool)
    admin = await create_test_user(pool)

    result = await service.resolve(
        dispute.id, admin, "DELISTED_AND_REFUND", "표시 성과와 실제 결과 불일치 확인"
    )

    assert result.listing_status == "DELISTED"
    async with pool.acquire() as conn:
        listing_status = await conn.fetchval(
            "SELECT status FROM strategy_listings WHERE id = $1", listing_id
        )
    assert listing_status == "DELISTED"


async def test_resolve_delisted_and_refund_credits_buyer_wallet(service, pool):
    dispute, _, _, buyer = await _open_dispute(pool)
    admin = await create_test_user(pool)
    balance_before = await _wallet_balance(pool, buyer)

    result = await service.resolve(
        dispute.id, admin, "DELISTED_AND_REFUND", "표시 성과와 실제 결과 불일치 확인"
    )

    assert result.refund_amount == _PURCHASE_PRICE
    assert await _wallet_balance(pool, buyer) == balance_before + _PURCHASE_PRICE


async def test_resolve_normal_risk_realization_does_not_refund(service, pool):
    dispute, _, _, buyer = await _open_dispute(pool)
    admin = await create_test_user(pool)
    balance_before = await _wallet_balance(pool, buyer)

    result = await service.resolve(
        dispute.id, admin, "NORMAL_RISK_REALIZATION", "정상적인 시장 리스크로 판단"
    )

    assert result.refund_amount is None
    assert await _wallet_balance(pool, buyer) == balance_before


async def test_resolve_records_audit_log(service, pool):
    dispute, listing_id, _, _ = await _open_dispute(pool)
    admin = await create_test_user(pool)

    await service.resolve(dispute.id, admin, "NORMAL_RISK_REALIZATION", "사유")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_data FROM audit_log "
            "WHERE action_type = 'dispute.resolved' AND target_id = $1",
            str(dispute.id),
        )
    assert row is not None
    decision_data = json.loads(row["decision_data"])
    assert decision_data["dispute_id"] == dispute.id


async def test_resolve_rejects_already_resolved_dispute(service, pool):
    dispute, _, _, _ = await _open_dispute(pool)
    admin = await create_test_user(pool)
    await service.resolve(dispute.id, admin, "NORMAL_RISK_REALIZATION", "사유")

    with pytest.raises(DisputeResolutionError):
        await service.resolve(dispute.id, admin, "NORMAL_RISK_REALIZATION", "재처리 시도")


async def test_concurrent_resolutions_only_one_succeeds(service, pool):
    """docs/RED_TEAM_FINDINGS.md #05 회귀 — conn.transaction()이 있어도
    READ COMMITTED에서는 두 관리자가 같은 분쟁을 거의 동시에 서로 다른
    결정으로 처리하면 하나가 조용히 덮어써졌다."""
    dispute, _, _, _ = await _open_dispute(pool)
    admin_a = await create_test_user(pool)
    admin_b = await create_test_user(pool)

    results = await asyncio.gather(
        service.resolve(dispute.id, admin_a, "NORMAL_RISK_REALIZATION", "정상 리스크"),
        service.resolve(dispute.id, admin_b, "DELISTED_AND_REFUND", "표시 성과 불일치"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, DisputeResolutionError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM disputes WHERE id = $1", dispute.id)
    assert status == "RESOLVED"


async def test_resolve_rejects_unknown_decision(service, pool):
    dispute, _, _, _ = await _open_dispute(pool)
    admin = await create_test_user(pool)

    with pytest.raises(DisputeResolutionError):
        await service.resolve(dispute.id, admin, "UNKNOWN", "사유")


async def test_get_detail_rejects_nonexistent_dispute(service):
    with pytest.raises(DisputeResolutionError):
        await service.get_detail(999999999)
