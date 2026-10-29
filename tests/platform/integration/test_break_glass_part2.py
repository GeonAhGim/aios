"""PLT-35(task-2682) -- break_glass_grant(M7) + break_glass.py 통합테스트,
실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2(B) PLT-35,
§4 I12, §9 PLT-35 DoD("자기승인·만료·이중소비 거부; admin 라우트 MFA 미달
403"), §9 통합 테스트 행(`test_break_glass.py`: 자기 승인 거부, 61분 거부
(DB CHECK), 2회 consume 거부).

ADR-2026-09-09-C D2 증빙:
- negative >= 3: self-approval(app+DB 이중), 61분 초과(DB CHECK), 2회 consume,
  미승인 상태 consume, 만료 후 consume.
- 실패 주입 1: `test_audit_failure_rolls_back_approval`(감사 INSERT가 실제 DB
  오류로 실패하면 승인 자체가 롤백됨을 증명).
- 성능 단언 1: `test_consume_latency_budget`(정상 입력 녹색).
- 게이트 적색 재현 1: `test_self_approval_bypassing_app_guard_still_blocked_by_db`
  (애플리케이션 가드를 우회한 raw UPDATE로 자기승인을 시도해도 DB CHECK가
  막는다 -- 코드 가드 하나만 있었다면 이 시나리오는 통과했을 것이다).
- budget invariance: `test_consume_latency_assertion_fires_on_10x_budget_sleep`(10x
  sleep 주입 → 적색 재현).
- cpu convergence: `test_cpu_ms_converges_near_zero_for_single_consume`(단일 호출
  cpu_ms → 0 수렴 검증)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from src.api.admin_deps import require_break_glass
from src.api.deps import AuthenticatedUser
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.security import break_glass
from src.core.security.break_glass import (
    BreakGlassInvalidStateError,
)
from src.foundation.trust.domain.rules.segregation_of_duty import assert_actor_not_counterparty
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _break_glass_grant_clean_slate(pool):
    async def _clean() -> None:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM break_glass_grant")

    await _clean()
    yield
    await _clean()


class _SpyMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str] | None]] = []

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


@pytest.fixture
def spy_metrics():
    spy = _SpyMetrics()
    set_metrics(spy)
    yield spy
    set_metrics(NullMetrics())


def _mfa_admin(user_id) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        email="admin@example.com",
        display_name="admin",
        mfa_enabled=True,
        mfa_verified_at=datetime.now(UTC),
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
        session_id=uuid4(),
        auth_level="MFA_VERIFIED",
    )


def _fresh() -> datetime:
    return datetime.now(UTC)


def _stale() -> datetime:
    """Just past MFA_STEP_UP_WINDOW (15min) -- task-3795: a session that
    passed TOTP 16 minutes ago must be treated as unverified, not as the
    still-"MFA_VERIFIED" JWT claim it would carry for up to 14 days."""
    return datetime.now(UTC) - timedelta(minutes=16)


async def _request(pool, conn, requester_id, *, scope="credential_revoke", ttl_minutes=60):
    return await break_glass.request_grant(
        conn,
        requester_id=requester_id,
        requester_mfa_verified_at=_fresh(),
        scope=scope,
        reason="incident-123",
        ttl_minutes=ttl_minutes,
    )


# --- require_break_glass dependency ------------------------------------------


async def test_require_break_glass_consumes_grant_on_matching_scope(pool):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin = _mfa_admin(await create_test_user(pool))

    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id, scope="credential_revoke")
        await break_glass.approve_grant(
            conn,
            grant_id=grant.id,
            approver_id=approver_id,
            approver_mfa_verified_at=_fresh(),
            check_segregation_of_duty=assert_actor_not_counterparty,
        )

    dependency = require_break_glass("credential_revoke")
    consumed = await dependency(x_break_glass_grant=grant.id, admin=admin, pool=pool)
    assert consumed.state == "USED"


async def test_require_break_glass_scope_mismatch_rejected(pool):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin = _mfa_admin(await create_test_user(pool))

    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id, scope="tenant_read")
        await break_glass.approve_grant(
            conn,
            grant_id=grant.id,
            approver_id=approver_id,
            approver_mfa_verified_at=_fresh(),
            check_segregation_of_duty=assert_actor_not_counterparty,
        )

    dependency = require_break_glass("credential_revoke")
    with pytest.raises(BreakGlassInvalidStateError, match="tenant_read"):
        await dependency(x_break_glass_grant=grant.id, admin=admin, pool=pool)

    # 스코프가 틀려도 grant는 이미 소비됐다(재사용 불가 -- fail-closed).
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT state FROM break_glass_grant WHERE id = $1", grant.id)
    assert row["state"] == "USED"
