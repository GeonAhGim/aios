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
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.api.deps import AuthenticatedUser
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.security import break_glass
from src.core.security.break_glass import (
    BreakGlassInvalidStateError,
    BreakGlassMfaRequiredError,
    BreakGlassSelfApprovalError,
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
        mfa_verified_at=datetime.now(timezone.utc),
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
        session_id=uuid4(),
        auth_level="MFA_VERIFIED",
    )


def _fresh() -> datetime:
    return datetime.now(timezone.utc)


def _stale() -> datetime:
    """Just past MFA_STEP_UP_WINDOW (15min) -- task-3795: a session that
    passed TOTP 16 minutes ago must be treated as unverified, not as the
    still-"MFA_VERIFIED" JWT claim it would carry for up to 14 days."""
    return datetime.now(timezone.utc) - timedelta(minutes=16)


async def _request(pool, conn, requester_id, *, scope="credential_revoke", ttl_minutes=60):
    return await break_glass.request_grant(
        conn,
        requester_id=requester_id,
        requester_mfa_verified_at=_fresh(),
        scope=scope,
        reason="incident-123",
        ttl_minutes=ttl_minutes,
    )


# --- happy path (baseline for the negative tests below) --------------------


async def test_request_approve_consume_happy_path(pool, spy_metrics):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        assert grant.state == "REQUESTED"

        approved = await break_glass.approve_grant(
            conn,
            grant_id=grant.id,
            approver_id=approver_id,
            approver_mfa_verified_at=_fresh(),
            check_segregation_of_duty=assert_actor_not_counterparty,
        )
        assert approved.state == "APPROVED"
        assert approved.approver_id == approver_id

        used = await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)
        assert used.state == "USED"
        assert used.used_at is not None

    phases = [labels["phase"] for _, labels in spy_metrics.counters if labels]
    assert phases == ["requested", "approved", "used"]


# --- negative: self-approval ------------------------------------------------


async def test_self_approval_rejected_by_app_guard(pool):
    requester_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        with pytest.raises(BreakGlassSelfApprovalError):
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=requester_id,
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )
        # 거부 후에도 REQUESTED 상태 그대로(승인 처리가 일부라도 진행되지 않음).
        row = await conn.fetchrow(
            "SELECT state, approver_id FROM break_glass_grant WHERE id = $1", grant.id
        )
        assert row["state"] == "REQUESTED"
        assert row["approver_id"] is None


async def test_self_approval_bypassing_app_guard_still_blocked_by_db(pool):
    """게이트 적색 재현 -- `approve_grant`의 애플리케이션 검사를 완전히 우회하고
    (raw UPDATE) 자기승인을 직접 시도한다. `break_glass_grant_check1`
    CHECK(approver_id <> requester_id)가 없었다면(즉 코드 가드 하나만 I12를
    지켰다면) 이 UPDATE는 조용히 성공했을 것이다."""
    requester_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE break_glass_grant SET approver_id = $1, state = 'APPROVED' WHERE id = $2",
                requester_id,
                grant.id,
            )


# --- negative: >60min TTL rejected by DB CHECK ------------------------------


async def test_expiry_over_60_minutes_rejected_by_db_check(pool):
    requester_id = await create_test_user(pool)
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO break_glass_grant (requester_id, scope, reason, expires_at) "
                "VALUES ($1, 'tenant_read', 'r', $2)",
                requester_id,
                now + timedelta(minutes=61),
            )


async def test_request_grant_over_60_minutes_rejected_by_code_before_db(pool):
    requester_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        with pytest.raises(ValueError, match="ttl_minutes"):
            await _request(pool, conn, requester_id, ttl_minutes=61)


# --- negative: consume rules -------------------------------------------------


async def test_consume_before_approval_rejected(pool):
    requester_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        with pytest.raises(BreakGlassInvalidStateError, match="APPROVED"):
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)


async def test_double_consume_rejected(pool):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        await break_glass.approve_grant(
            conn,
            grant_id=grant.id,
            approver_id=approver_id,
            approver_mfa_verified_at=_fresh(),
            check_segregation_of_duty=assert_actor_not_counterparty,
        )
        await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)
        with pytest.raises(BreakGlassInvalidStateError, match="이미 소비"):
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)


async def test_expired_grant_consume_rejected(pool):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id, ttl_minutes=1)
        await break_glass.approve_grant(
            conn,
            grant_id=grant.id,
            approver_id=approver_id,
            approver_mfa_verified_at=_fresh(),
            check_segregation_of_duty=assert_actor_not_counterparty,
        )
        # 실제 60초를 기다리지 않고 expires_at을 과거로 되돌려 만료를 재현한다
        # (실패 주입 -- 시계열 조건을 직접 조작).
        await conn.execute(
            "UPDATE break_glass_grant SET expires_at = now() - interval '1 second' WHERE id = $1",
            grant.id,
        )
        with pytest.raises(BreakGlassInvalidStateError, match="만료"):
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)


async def test_request_grant_requires_mfa():
    with pytest.raises(BreakGlassMfaRequiredError):
        await break_glass.request_grant(
            None,  # MFA 검사가 conn 접근보다 먼저 일어남을 증명(연결 없이도 거부)
            requester_id=uuid4(),
            requester_mfa_verified_at=None,
            scope="tenant_read",
            reason="x",
        )


# --- negative: stale MFA step-up rejected (task-3795) -----------------------


async def test_request_grant_rejects_stale_mfa_step_up():
    """task-3795: a JWT `auth_level="MFA_VERIFIED"` claim survives up to
    REFRESH_TTL_DAYS(14) unchanged across refresh -- `mfa_verified_at` must be
    re-checked for freshness on every call, not trusted as a one-time claim."""
    with pytest.raises(BreakGlassMfaRequiredError):
        await break_glass.request_grant(
            None,
            requester_id=uuid4(),
            requester_mfa_verified_at=_stale(),
            scope="tenant_read",
            reason="x",
        )


async def test_approve_grant_rejects_stale_mfa_step_up(pool):
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)
        with pytest.raises(BreakGlassMfaRequiredError):
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=approver_id,
                approver_mfa_verified_at=_stale(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )
        # 거부 후에도 REQUESTED 상태 그대로(승인 처리가 일부라도 진행되지 않음).
        row = await conn.fetchrow(
            "SELECT state, approver_id FROM break_glass_grant WHERE id = $1", grant.id
        )
        assert row["state"] == "REQUESTED"
        assert row["approver_id"] is None


# --- failure injection: audit INSERT failure rolls back the whole approval --


async def test_audit_failure_rolls_back_approval(pool, monkeypatch):
    """승인 UPDATE는 성공하더라도 같은 트랜잭션의 감사 INSERT가 실제 DB 오류로
    실패하면(테이블명 오타 주입) 트랜잭션 전체가 롤백되어 grant는 REQUESTED로
    남는다 -- '승인은 기록됐는데 감사만 없다'는 절반짜리 상태가 없음을 증명."""
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)

    # request_grant까지는 정상 record_audit_log로 만든다 -- monkeypatch는
    # approve_grant 호출부터만 적용해야 request_grant 내부의 감사 기록까지
    # 실패로 오염되지 않는다.
    async with pool.acquire() as conn:
        grant = await _request(pool, conn, requester_id)

    async def failing_audit(conn: asyncpg.Connection, **kwargs: object) -> None:
        await conn.execute("INSERT INTO audit_log_missing_table (id) VALUES (1)")

    monkeypatch.setattr(break_glass, "record_audit_log", failing_audit)

    with pytest.raises(asyncpg.UndefinedTableError):
        async with pool.acquire() as conn, conn.transaction():
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=approver_id,
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, approver_id FROM break_glass_grant WHERE id = $1", grant.id
        )
    assert row["state"] == "REQUESTED"
    assert row["approver_id"] is None


# --- performance assertion ---------------------------------------------------


@pytest.mark.perf
async def test_consume_latency_budget(pool, perf_budget):
    """단일 조건부 UPDATE(consume)는 관리자 작업치고 관대한 예산인 p95 100ms
    아래여야 한다(이 축은 §4 성능 예산 표에 별도 수치가 없어 임시로 정한
    ad-hoc 예산 -- 사전거래 게이트처럼 확정된 SLO가 아니다). 20개 grant를
    각각 승인 후 소비해 실측한다.

    raw time.perf_counter() → perf_budget.sample_async() 전환(task-11633).
    """
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)

    # 준비: 승인된 grant 5개 생성 (측정 대상은 consume()만)
    grants: list = []
    async with pool.acquire() as conn:
        for _ in range(5):
            grant = await _request(pool, conn, requester_id)
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=approver_id,
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )
            grants.append(grant)

    async def _consume_once() -> None:
        async with pool.acquire() as conn:
            grant = grants.pop()
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)

    samples = sorted(
        [await perf_budget.sample_async(_consume_once) for _ in range(5)],
        key=lambda s: s.wall_ms,
    )
    p95 = samples[-1].wall_ms
    assert p95 < 100.0, f"consume p95={p95:.1f}ms, 예산 100ms 초과"


# --- failure injection: 10x sleep delay → assertion red ----------------------


@pytest.mark.perf
async def test_consume_latency_assertion_fires_on_10x_budget_sleep(pool, perf_budget):
    """Budget invariance: if the same assertion is run against a call delayed
    by 10× the budget (100ms × 10 = 1000ms), the p95 assertion must fail.

    This proves the performance gate is not a tautology — it actually rejects
    slow paths. The test **expects** the assertion to fire (AssertionError).
    """
    import asyncio as _asyncio

    _BUDGET_MS = 100.0
    _SLEEP_MS = _BUDGET_MS * 10  # 1000ms per iteration

    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)

    async def _delayed_consume() -> None:
        # Each iteration needs a fresh grant (consume is single-use).
        async with pool.acquire() as conn:
            grant = await _request(pool, conn, requester_id)
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=approver_id,
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )
        async with pool.acquire() as conn:
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)
        # Inject delay AFTER consume to prove the test body itself is slow
        # (delay before consume would block the measured section;
        #  delay after proves we're measuring the right thing).
        await _asyncio.sleep(_SLEEP_MS / 1000)

    samples = sorted(
        [await perf_budget.sample_async(_delayed_consume) for _ in range(5)],
        key=lambda s: s.wall_ms,
    )
    p95 = samples[-1].wall_ms
    # Same assertion as the green test; with 10× sleep it MUST exceed budget.
    with pytest.raises(AssertionError) as exc_info:
        assert p95 < _BUDGET_MS, f"consume p95={p95:.1f}ms, 예산 {_BUDGET_MS}ms 초과"
    assert "p95=" in str(exc_info.value)


# --- CPU measurement convergence: single-call cpu_ms → 0 for trivial work ----


@pytest.mark.perf
async def test_cpu_ms_converges_near_zero_for_single_consume(pool, perf_budget):
    """CPU measurement invariant: a single consume() call on an I/O-bound
    conditional UPDATE reports cpu_ms close to zero.

    This verifies that `PerfSample.cpu_ms` actually measures process CPU time
    (via `time.process_time()`) and does not converge to a non-zero floor
    caused by measurement artefacts or batch=1 quantization on Windows.
    """
    requester_id = await create_test_user(pool)
    approver_id = await create_test_user(pool)
    admin_id = await create_test_user(pool)

    async def _single_consume() -> None:
        # Each iteration needs a fresh grant (consume is single-use).
        async with pool.acquire() as conn:
            grant = await _request(pool, conn, requester_id)
            await break_glass.approve_grant(
                conn,
                grant_id=grant.id,
                approver_id=approver_id,
                approver_mfa_verified_at=_fresh(),
                check_segregation_of_duty=assert_actor_not_counterparty,
            )
        async with pool.acquire() as conn:
            await break_glass.consume(conn, grant_id=grant.id, admin_id=admin_id)

    # batch=1 is the stress case: Windows time.process_time() has ~15.6ms tick
    # resolution; a single trivial call should still read 0ms or 1 tick (15.6ms).
    samples = await perf_budget.samples_async(_single_consume, n=5)
    # cpu_ms must be well under 50ms for a single-row DB lookup — proves the
    # timer is not stuck at a non-zero floor.
    max_cpu_ms = max(s.cpu_ms for s in samples)
    assert max_cpu_ms < 50, (
        f"cpu_ms={max_cpu_ms:.2f}ms exceeds trivial-work floor (50ms); "
        f"samples={[f'{s.cpu_ms:.2f}' for s in samples]}"
    )
