"""PLT-integration 패키지 마커 DEEPEN(task-9961, 원 리프 task-6704) — negative
test 0건이던 이 파일에 `request_grant`(I12) 경로의 negative/실패주입/성능
케이스를 추가한다.

`test_break_glass.py`는 `approve_grant`/`consume` 축을 이미 D2/D3까지 덮는다
(자기승인, 61분 초과, 이중소비, 만료, MFA stale, `approve_grant`의 감사
INSERT 실패 롤백) — 여기서는 그 파일이 다루지 않는 `request_grant` 자체의
축(잘못된 scope, 존재하지 않는 grant에 대한 approve/consume, request_grant의
감사 INSERT 실패 롤백, request_grant p95)만 보강해 중복을 피한다.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.core.security import break_glass
from src.core.security.break_glass import BreakGlassInvalidStateError
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


def _fresh() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_request_grant_invalid_scope_rejected_by_db_check(pool):
    """I12 밖의 `scope` 값은 코드 가드가 없다 -- `break_glass_grant_scope_check`
    CHECK 하나가 유일한 방어선임을 직접 증명한다."""
    requester_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO break_glass_grant (requester_id, scope, reason, expires_at) "
                "VALUES ($1, 'not_a_real_scope', 'r', $2)",
                requester_id,
                _fresh() + timedelta(minutes=10),
            )


async def test_approve_nonexistent_grant_rejected(pool):
    async with pool.acquire() as conn:
        with pytest.raises(BreakGlassInvalidStateError, match="존재하지 않습니다"):
            await break_glass.approve_grant(
                conn,
                grant_id=uuid4(),
                approver_id=await create_test_user(pool),
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=lambda *a, **k: None,
            )


async def test_consume_nonexistent_grant_rejected(pool):
    async with pool.acquire() as conn:
        with pytest.raises(BreakGlassInvalidStateError, match="존재하지 않습니다"):
            await break_glass.consume(conn, grant_id=uuid4(), admin_id=await create_test_user(pool))


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_audit_failure_rolls_back_request_grant(pool, monkeypatch):
    """`test_break_glass.py::test_audit_failure_rolls_back_approval`은 approve_grant
    축만 증명한다 -- 여기서는 request_grant의 감사 INSERT가 같은 방식으로
    실패할 때 INSERT 자체가 롤백되어 grant 행이 전혀 남지 않음을 증명한다."""
    requester_id = await create_test_user(pool)

    async def failing_audit(conn: asyncpg.Connection, **kwargs: object) -> None:
        await conn.execute("INSERT INTO audit_log_missing_table (id) VALUES (1)")

    monkeypatch.setattr(break_glass, "record_audit_log", failing_audit)

    with pytest.raises(asyncpg.UndefinedTableError):
        async with pool.acquire() as conn, conn.transaction():
            await break_glass.request_grant(
                conn,
                requester_id=requester_id,
                requester_mfa_verified_at=_fresh(),
                scope="tenant_read",
                reason="incident-rollback",
            )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM break_glass_grant WHERE requester_id = $1", requester_id
        )
    assert row is None


# ---------------------------------------------------------------------------
# Performance assertion
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_request_grant_latency_budget(pool):
    """단일 INSERT+감사 기록(request_grant)은 관리자 작업치고 관대한 예산인
    p95 100ms 아래여야 한다(§4 성능 예산 표에 별도 수치가 없는 ad-hoc 예산,
    `test_break_glass.py::test_consume_latency_budget`과 동일 기준)."""
    requester_id = await create_test_user(pool)

    durations: list[float] = []
    async with pool.acquire() as conn:
        for _ in range(20):
            started = time.perf_counter()
            await break_glass.request_grant(
                conn,
                requester_id=requester_id,
                requester_mfa_verified_at=_fresh(),
                scope="tenant_read",
                reason="perf-probe",
            )
            durations.append(time.perf_counter() - started)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.1, f"request_grant() p95={p95 * 1000:.1f}ms, 예산 100ms 초과"
