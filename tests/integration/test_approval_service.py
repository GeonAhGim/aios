"""10.1/10.4 통합테스트 — 실제 dev DB 대상.

mandatory_wait_seconds(60/180초) 실제 대기 대신, DB의 created_at을 직접
과거로 돌려 "대기시간이 지난 상태"를 결정적으로 재현한다.
"""
import asyncio
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
        user_id=await create_test_user(pool),
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
        user_id=await create_test_user(pool),
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
        user_id=await create_test_user(pool),
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
        user_id=await create_test_user(pool),
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
        user_id=await create_test_user(pool),
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


async def test_concurrent_solo_approvals_only_one_succeeds(pool):
    """docs/RED_TEAM_FINDINGS.md #04 회귀 — approve()가 "읽고 나서 별도로
    쓰기"였을 때는 거의 동시에 들어온 두 승인이 둘 다 통과할 수 있었다."""
    request = await create_request(
        pool,
        scope="USER",
        user_id=await create_test_user(pool),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
    )
    await _rewind_created_at(pool, request.id, 61)

    approver_a, approver_b = uuid4(), uuid4()
    results = await asyncio.gather(
        approve(pool, request.id, approver_a),
        approve(pool, request.id, approver_b),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ApprovalError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, first_approver_id FROM approval_requests WHERE id = $1", request.id
        )
    assert row["status"] == "APPROVED"
    assert row["first_approver_id"] == successes[0].first_approver_id


async def test_concurrent_dual_first_signature_only_one_succeeds(pool, monkeypatch):
    """docs/RED_TEAM_FINDINGS.md #04 회귀 — DUAL 첫 서명 레이스에서 나중에
    커밋되는 쪽이 앞선 서명자를 조용히 덮어쓸 수 있었다(독립된 두 사람의
    서명이라는 전제가 타이밍만으로 깨짐).

    asyncio.gather만으로는 두 approve() 호출의 초기 조회(_fetch)가 실제로
    동시에 겹치는 보장이 없다 — 커넥션 풀 라운드트립이 우연히 어긋나면
    첫 번째 호출이 완전히 끝난 뒤 두 번째가 시작돼(첫서명→둘째서명 정상
    흐름) 레이스 자체가 재현되지 않을 수 있다. barrier로 두 호출의
    `_fetch()`가 반드시 같은 시점에 끝나도록 강제해 "둘 다
    first_approver_id=None을 봤다"는 원래 레이스 조건을 결정적으로
    재현한다."""
    import src.core.approval.service as approval_service

    request = await create_request(
        pool,
        scope="USER",
        user_id=await create_test_user(pool),
        trigger_source="execution_high_allocation",
        requested_action="START_LIVE_EXECUTION",
        context={},
        approval_mode="DUAL",
    )
    await _rewind_created_at(pool, request.id, 61)

    arrived = 0
    released = asyncio.Event()
    original_fetch = approval_service._fetch

    async def _synced_fetch(pool_, request_id):
        nonlocal arrived
        result = await original_fetch(pool_, request_id)
        arrived += 1
        if arrived >= 2:
            released.set()
        else:
            await released.wait()
        return result

    monkeypatch.setattr(approval_service, "_fetch", _synced_fetch)

    approver_a, approver_b = uuid4(), uuid4()
    results = await asyncio.gather(
        approve(pool, request.id, approver_a),
        approve(pool, request.id, approver_b),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ApprovalError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, first_approver_id FROM approval_requests WHERE id = $1", request.id
        )
    assert row["status"] == "PENDING"
    assert row["first_approver_id"] == successes[0].first_approver_id


async def test_context_roundtrips_with_decimal_values(pool):
    from decimal import Decimal

    request = await create_request(
        pool,
        scope="USER",
        user_id=await create_test_user(pool),
        trigger_source="watchdog_liquidate",
        requested_action="LIQUIDATE_POSITION",
        context={"loss_pct": Decimal("7.25")},
        approval_mode="SOLO",
    )
    assert request.context["loss_pct"] == "7.25"
