"""10.1/10.4 통합테스트 — 실제 dev DB 대상.

mandatory_wait_seconds(60/180초) 실제 대기 대신, DB의 created_at을 직접
과거로 돌려 "대기시간이 지난 상태"를 결정적으로 재현한다.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval.service import (
    ApprovalError,
    approve,
    cancel,
    create_request,
    expire_pending,
    reject,
)


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


async def _rewind_created_at(pool, request_id: int, seconds_ago: float) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = $2 WHERE id = $1",
            request_id,
            datetime.now(timezone.utc) - timedelta(seconds=seconds_ago),
        )


async def test_solo_approval_blocked_before_mandatory_wait(pool):
    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={"symbol": "BTC/USDT"},
        approval_mode="SOLO",
    )
    with pytest.raises(ApprovalError):
        await approve(pool, request.id, uuid4())


async def test_solo_approval_succeeds_after_wait_elapsed(pool):
    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={"symbol": "BTC/USDT"},
        approval_mode="SOLO",
    )
    await _rewind_created_at(pool, request.id, 61)

    approver = uuid4()
    result = await approve(pool, request.id, approver)

    assert result.status == "APPROVED"
    assert result.first_approver_id == approver


async def test_dual_approval_requires_two_different_accounts(pool):
    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="DUAL",
    )
    await _rewind_created_at(pool, request.id, 61)

    approver1 = uuid4()
    after_first = await approve(pool, request.id, approver1)
    assert after_first.status == "PENDING"
    assert after_first.first_approver_id == approver1

    with pytest.raises(ApprovalError):
        await approve(pool, request.id, approver1)  # 동일 계정 재서명 거부

    approver2 = uuid4()
    after_second = await approve(pool, request.id, approver2)
    assert after_second.status == "APPROVED"
    assert after_second.second_approver_id == approver2


async def test_platform_scope_uses_180_second_wait(pool):
    request = await create_request(
        pool,
        scope="PLATFORM",
        trigger_source="circuit_breaker_reactivation",
        requested_action="REACTIVATE",
        context={},
        approval_mode="SOLO",
    )
    assert request.mandatory_wait_seconds == 180

    await _rewind_created_at(pool, request.id, 61)  # 60초는 지났지만 180초는 아직
    with pytest.raises(ApprovalError):
        await approve(pool, request.id, uuid4())


async def test_expire_pending_auto_rejects_stale_requests(pool):
    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET expires_at = now() - interval '1 second' WHERE id = $1",
            request.id,
        )

    expired_ids = await expire_pending(pool)

    assert request.id in expired_ids


async def test_reject_marks_status_rejected(pool):
    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
    )
    result = await reject(pool, request.id, uuid4())
    assert result.status == "REJECTED"


async def test_cancel_marks_status_cancelled(pool):
    request = await create_request(
        pool,
        scope="PLATFORM",
        trigger_source="circuit_breaker_reactivation",
        requested_action="REACTIVATE",
        context={},
        approval_mode="SOLO",
    )
    result = await cancel(pool, request.id)
    assert result.status == "CANCELLED"


async def test_context_roundtrips_with_decimal_values(pool):
    from decimal import Decimal

    request = await create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={"loss_pct": Decimal("7.25")},
        approval_mode="SOLO",
    )
    assert request.context["loss_pct"] == "7.25"
