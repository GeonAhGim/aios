"""13.10 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.dispute_service import DisputeError, DisputeService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
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


@pytest.fixture
def service(pool):
    return DisputeService(pool)


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


async def _always_eligible(strategy_id, version):
    return True


async def _purchase(pool, seller, buyer):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    purchase_service = PurchaseService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    result = await purchase_service.purchase(buyer, approved.listing_id)
    return result.purchase_id


async def test_submit_creates_open_dispute(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    purchase_id = await _purchase(pool, seller, buyer)

    dispute = await service.submit(buyer, purchase_id, "표시된 성과와 실제 결과가 다릅니다")

    assert dispute.status == "OPEN"
    assert dispute.purchase_id == purchase_id


async def test_dispute_immediately_visible_for_future_admin_queue(service, pool):
    """완료조건 실증 — FD-18.2가 나중에 조회할 대상(status='OPEN')에 즉시
    노출된다. FD-18.2 자체(운영자 조회 API)는 아직 없어 직접 쿼리로 검증."""
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    purchase_id = await _purchase(pool, seller, buyer)
    dispute = await service.submit(buyer, purchase_id, "사유")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM disputes WHERE status = 'OPEN' AND id = $1", dispute.id
        )
    assert row is not None


async def test_submit_rejects_other_users_purchase(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    stranger = await create_test_user(pool)
    purchase_id = await _purchase(pool, seller, buyer)

    with pytest.raises(DisputeError):
        await service.submit(stranger, purchase_id, "이건 내 구매가 아님")


async def test_submit_rejects_nonexistent_purchase(service, pool):
    buyer = await create_test_user(pool)

    with pytest.raises(DisputeError):
        await service.submit(buyer, 999999999, "사유")


async def test_submit_rejects_empty_reason(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    purchase_id = await _purchase(pool, seller, buyer)

    with pytest.raises(DisputeError):
        await service.submit(buyer, purchase_id, "   ")


async def test_duplicate_open_dispute_rejected(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    purchase_id = await _purchase(pool, seller, buyer)
    await service.submit(buyer, purchase_id, "첫 번째 분쟁")

    with pytest.raises(DisputeError):
        await service.submit(buyer, purchase_id, "두 번째 분쟁 시도")
