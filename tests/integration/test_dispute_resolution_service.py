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


async def test_concurrent_resolutions_only_one_succeeds(service, pool, monkeypatch):
    """docs/RED_TEAM_FINDINGS.md #05 회귀 — conn.transaction()이 있어도
    READ COMMITTED에서는 두 관리자가 같은 분쟁을 거의 동시에 서로 다른
    결정으로 처리하면 하나가 조용히 덮어써졌다.

    asyncio.gather만으로는 두 resolve() 호출의 get_detail() 조회가 실제로
    동시에 겹친다는 보장이 없다 — #04/#05(test_verification_service.py)와
    같은 원칙으로 barrier를 걸어 원래 레이스 조건을 결정적으로 재현한다."""
    dispute, _, _, _ = await _open_dispute(pool)
    admin_a = await create_test_user(pool)
    admin_b = await create_test_user(pool)

    arrived = 0
    released = asyncio.Event()
    original_get_detail = type(service).get_detail

    async def _synced_get_detail(self, dispute_id):
        nonlocal arrived
        result = await original_get_detail(self, dispute_id)
        arrived += 1
        if arrived >= 2:
            released.set()
        else:
            await released.wait()
        return result

    monkeypatch.setattr(type(service), "get_detail", _synced_get_detail)

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


async def test_refund_is_credited_only_once_across_disputes(service, pool):
    """전수감사 §2 — RESOLVED 뒤 같은 구매에 새 분쟁을 열어 다시
    DELISTED_AND_REFUND해도 환불은 한 번만 적립되고, 두 번째 분쟁은 OPEN으로
    남는다(트랜잭션 전체 롤백)."""
    dispute, _, _, buyer = await _open_dispute(pool)
    admin = await create_test_user(pool)

    first = await service.resolve(dispute.id, admin, "DELISTED_AND_REFUND", "성과 불일치")
    assert first.refund_amount == _PURCHASE_PRICE
    balance_after_first = await _wallet_balance(pool, buyer)
    assert balance_after_first == _PURCHASE_PRICE

    second_dispute = await DisputeService(pool).submit(buyer, dispute.purchase_id, "재차 제기")
    with pytest.raises(DisputeResolutionError, match="이미 환불"):
        await service.resolve(second_dispute.id, admin, "DELISTED_AND_REFUND", "재환불 시도")

    assert await _wallet_balance(pool, buyer) == balance_after_first
    async with pool.acquire() as conn:
        second_status = await conn.fetchval(
            "SELECT status FROM disputes WHERE id = $1", second_dispute.id
        )
        refund_count = await conn.fetchval(
            "SELECT COUNT(*) FROM wallet_transactions "
            "WHERE related_purchase_id = $1 AND tx_type = 'REFUND'",
            dispute.purchase_id,
        )
    assert second_status == "OPEN"
    assert refund_count == 1
