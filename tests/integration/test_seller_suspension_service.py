"""18.4 통합테스트 — 실제 dev DB 대상.

완료조건(정지된 판매자의 신규 리스팅 거부)은 ListingService(13.2)와의
실제 연동으로 실증한다.
"""

import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingError, ListingService
from src.services.seller_suspension_service import SellerSuspensionError, SellerSuspensionService
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
    return SellerSuspensionService(pool)


async def _always_eligible(strategy_id, version, seller_user_id=None):
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


async def test_suspend_sets_flag(service, pool):
    seller = await create_test_user(pool)
    admin = await create_test_user(pool)

    result = await service.suspend(seller, admin, "반복 위반")

    assert result.seller_suspended is True
    async with pool.acquire() as conn:
        flag = await conn.fetchval("SELECT seller_suspended FROM users WHERE user_id = $1", seller)
    assert flag is True


async def test_resuspending_is_idempotent(service, pool):
    seller = await create_test_user(pool)
    admin = await create_test_user(pool)
    await service.suspend(seller, admin, "1차 사유")

    result = await service.suspend(seller, admin, "2차 사유(재정지 시도)")

    assert result.seller_suspended is True


async def test_suspend_records_audit_log(service, pool):
    seller = await create_test_user(pool)
    admin = await create_test_user(pool)

    await service.suspend(seller, admin, "허위 백테스트 결과")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT decision_data FROM audit_log "
            "WHERE action_type = 'seller.suspended' AND target_id = $1",
            str(seller),
        )
    assert row is not None
    decision_data = json.loads(row["decision_data"])
    assert decision_data["reason"] == "허위 백테스트 결과"


async def test_suspend_rejects_nonexistent_user(service, pool):
    import uuid

    admin = await create_test_user(pool)
    with pytest.raises(SellerSuspensionError):
        await service.suspend(uuid.uuid4(), admin, "사유")


async def test_suspend_rejects_nonexistent_user_without_audit_trace(service, pool):
    """불변식: 존재하지 않는 사용자에 대한 정지 시도는 감사로그(WORM)도
    남기지 않는다 — 실패한 시도가 audit_log에 흔적을 남기면 8.10 원칙
    (감사로그는 실제로 일어난 결정만 기록)을 위반한다."""
    import uuid

    admin = await create_test_user(pool)
    ghost_user_id = uuid.uuid4()

    with pytest.raises(SellerSuspensionError):
        await service.suspend(ghost_user_id, admin, "사유")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM audit_log WHERE action_type = 'seller.suspended' AND target_id = $1",
            str(ghost_user_id),
        )
    assert row is None


async def test_suspend_rolls_back_flag_when_audit_log_write_fails(service, pool, monkeypatch):
    """실패주입: record_audit_log가 예외를 던지면 트랜잭션 전체가 롤백되어
    seller_suspended 플래그도 갱신되지 않아야 한다 — UPDATE와 감사로그 기록이
    같은 트랜잭션에 있다는 원자성 불변식을 검증한다."""
    seller = await create_test_user(pool)
    admin = await create_test_user(pool)

    async def _boom(*args, **kwargs):
        raise RuntimeError("audit_log write failure (injected)")

    monkeypatch.setattr("src.services.seller_suspension_service.record_audit_log", _boom)

    with pytest.raises(RuntimeError, match="injected"):
        await service.suspend(seller, admin, "반복 위반")

    async with pool.acquire() as conn:
        flag = await conn.fetchval("SELECT seller_suspended FROM users WHERE user_id = $1", seller)
    assert flag is False


async def test_suspended_seller_cannot_create_new_listing(service, pool):
    seller = await create_test_user(pool)
    admin = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)

    await service.suspend(seller, admin, "반복 위반")

    with pytest.raises(ListingError):
        await listing_service.create_listing(seller, strategy_id, version, None)


async def test_non_suspended_seller_can_still_create_listing(service, pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)

    listing = await listing_service.create_listing(seller, strategy_id, version, None)

    assert listing.status == "DRAFT"
