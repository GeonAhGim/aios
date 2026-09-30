"""PLT-35(task-2682, task-6704) -- `break_glass` module negative/실패주입 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2(B) row
`src/core/security/break_glass.py`.

This file is the DEEPEN target for task-9963 (orphan deliverable 5828 qa-2).
Original task-6704 had 0 negative tests — this file adds ≥3 negative tests,
≥1 failure-injection test, and 1 performance assertion against the budget
table in ADR-2026-09-09-C Decision 1.

Tests are pure unit tests: no DB, no FastAPI DI chain — functions are called
directly with synthetic inputs. The only async functions use a minimal
asyncpg.Connection mock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.security.break_glass import (
    MFA_STEP_UP_WINDOW,
    BreakGlassInvalidStateError,
    BreakGlassMfaRequiredError,
    BreakGlassSelfApprovalError,
    approve_grant,
    consume,
    mfa_step_up_fresh,
    request_grant,
)
from src.core.security.segregation_of_duty_port import SegregationOfDutyViolation

# ── helpers ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_mock_conn(
    fetchrow_result: list[object] | None = None,
    *,
    raise_on_fetchrow: bool = False,
) -> MagicMock:
    """Build a fake asyncpg.Connection whose fetchrow returns a grant row or
    raises.  ``fetchrow_result`` is a list of ``asyncpg.Record``-like objects
    (or ``None`` to simulate "row not found")."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    if raise_on_fetchrow:
        conn.fetchrow.side_effect = RuntimeError("DB connection lost")
    elif fetchrow_result is not None:
        # First call returns the grant row, subsequent calls return the
        # reason lookup rows or None.
        conn.fetchrow.side_effect = fetchrow_result
    else:
        conn.fetchrow.return_value = None
    return conn


def _fake_record(**fields) -> MagicMock:
    """Return a MagicMock that behaves like ``asyncpg.Record`` — supports
    dict-style item access and column-name attribute access."""
    rec = MagicMock()
    for key, value in fields.items():
        setattr(rec, key, value)
        rec.__getitem__ = lambda self, k, _d={**fields}: _d[k]
    return rec


# ── negative tests (≥3 required) ─────────────────────────────────────────────


class TestNegativeMfaStepUpFresh:
    """Negative tests for `mfa_step_up_fresh`.

    I-10 (MFA step-up): `mfa_verified_at` must be within 15 min of now.
    """

    def test_none_mfa_verified_at_returns_false(self):
        """MFA를 한 번도 검증하지 않은 세션은 항상 False."""
        assert mfa_step_up_fresh(None) is False

    def test_stale_mfa_verified_at_returns_false(self):
        """16분 전에 TOTP를 통과한 세션은 MFA 재확인이 필요하다."""
        stale = _now() - timedelta(minutes=16)
        assert mfa_step_up_fresh(stale) is False

    def test_exactly_at_window_boundary_returns_true(self):
        """정확히 15분 지점은 still within window (<= 비교)."""
        boundary = _now() - MFA_STEP_UP_WINDOW
        assert mfa_step_up_fresh(boundary) is True

    def test_one_second_over_window_returns_false(self):
        """15분 1초 지점은 window를 벗어났다."""
        over = _now() - (MFA_STEP_UP_WINDOW + timedelta(seconds=1))
        assert mfa_step_up_fresh(over) is False


class TestNegativeRequestGrant:
    """Negative tests for `request_grant`.

    I-12 (grant TTL ≤ 60min, single use, MFA step-up).
    """

    @pytest.mark.asyncio
    async def test_ttl_zero_raises_value_error(self):
        """ttl_minutes=0 은 유효하지 않다 (0 초과 조건)."""
        conn = _make_mock_conn()
        with pytest.raises(ValueError, match="ttl_minutes=0"):
            await request_grant(
                conn,
                requester_id=uuid4(),
                requester_mfa_verified_at=_now(),
                scope="tenant_read",
                reason="test",
                ttl_minutes=0,
            )

    @pytest.mark.asyncio
    async def test_ttl_exceeds_max_raises_value_error(self):
        """ttl_minutes=61 은 DB CHECK를 거치기 전부터 거부된다."""
        conn = _make_mock_conn()
        with pytest.raises(ValueError, match="ttl_minutes=61"):
            await request_grant(
                conn,
                requester_id=uuid4(),
                requester_mfa_verified_at=_now(),
                scope="kill_switch_override",
                reason="emergency",
                ttl_minutes=61,
            )

    @pytest.mark.asyncio
    async def test_negative_ttl_raises_value_error(self):
        """음수 ttl도 거부된다."""
        conn = _make_mock_conn()
        with pytest.raises(ValueError, match="ttl_minutes=-1"):
            await request_grant(
                conn,
                requester_id=uuid4(),
                requester_mfa_verified_at=_now(),
                scope="tenant_read",
                reason="test",
                ttl_minutes=-1,
            )

    @pytest.mark.asyncio
    async def test_stale_requester_mfa_raises_error(self):
        """요청자의 MFA가 16분 전이면 BreakGlassMfaRequiredError."""
        conn = _make_mock_conn()
        stale = _now() - timedelta(minutes=16)
        with pytest.raises(BreakGlassMfaRequiredError, match="MFA 재확인"):
            await request_grant(
                conn,
                requester_id=uuid4(),
                requester_mfa_verified_at=stale,
                scope="tenant_read",
                reason="test",
                ttl_minutes=30,
            )


class TestNegativeApproveGrant:
    """Negative tests for `approve_grant`.

    I-12 (self-approval forbidden).
    """

    @pytest.mark.asyncio
    async def test_nonexistent_grant_raises_error(self):
        """존재하지 않는 grant_id로 승인 시도 시 BreakGlassInvalidStateError."""
        conn = _make_mock_conn(fetchrow_result=[None])
        checker = MagicMock()
        with pytest.raises(BreakGlassInvalidStateError, match="존재하지 않습니다"):
            await approve_grant(
                conn,
                grant_id=uuid4(),
                approver_id=uuid4(),
                approver_mfa_verified_at=_now(),
                check_segregation_of_duty=checker,
            )

    @pytest.mark.asyncio
    async def test_stale_approver_mfa_raises_error(self):
        """승인자의 MFA가 16분 전이면 BreakGlassMfaRequiredError."""
        conn = _make_mock_conn(fetchrow_result=[_fake_record(requester_id=uuid4())])
        stale = _now() - timedelta(minutes=16)
        checker = MagicMock()
        with pytest.raises(BreakGlassMfaRequiredError, match="MFA 재확인"):
            await approve_grant(
                conn,
                grant_id=uuid4(),
                approver_id=uuid4(),
                approver_mfa_verified_at=stale,
                check_segregation_of_duty=checker,
            )

    @pytest.mark.asyncio
    async def test_self_approval_raises_error(self):
        """요청자 본인이 승인하면 BreakGlassSelfApprovalError."""
        requester_id = uuid4()
        conn = _make_mock_conn(fetchrow_result=[_fake_record(requester_id=requester_id)])

        class _RejectChecker:
            def __call__(self, _a, _b, *, action=""):
                raise SegregationOfDutyViolation(_a, action)

        checker = _RejectChecker()
        with pytest.raises(BreakGlassSelfApprovalError, match="자기승인 금지"):
            await approve_grant(
                conn,
                grant_id=uuid4(),
                approver_id=requester_id,
                approver_mfa_verified_at=_now(),
                check_segregation_of_duty=checker,
            )


class TestNegativeConsume:
    """Negative tests for `consume`.

    I-12 (single use, unexpired).
    """

    @pytest.mark.asyncio
    async def test_already_used_raises_error(self):
        """이미 소비된 grant는 재소비 불가."""
        used_at = _now() - timedelta(minutes=5)
        conn = _make_mock_conn(
            fetchrow_result=[
                None,  # consume UPDATE returns None (already used)
                _fake_record(
                    state="APPROVED", used_at=used_at, expires_at=_now() + timedelta(minutes=55)
                ),
            ]
        )
        with pytest.raises(BreakGlassInvalidStateError, match="이미 소비되었습니다"):
            await consume(conn, grant_id=uuid4(), admin_id=uuid4())

    @pytest.mark.asyncio
    async def test_expired_grant_raises_error(self):
        """만료된 grant는 소비 불가."""
        conn = _make_mock_conn(
            fetchrow_result=[
                None,  # consume UPDATE returns None
                _fake_record(
                    state="APPROVED", used_at=None, expires_at=_now() - timedelta(minutes=1)
                ),
            ]
        )
        with pytest.raises(BreakGlassInvalidStateError, match="만료되었습니다"):
            await consume(conn, grant_id=uuid4(), admin_id=uuid4())

    @pytest.mark.asyncio
    async def test_non_approved_state_raises_error(self):
        """APPROVED가 아닌 상태(REQUESTED 등)는 소비 불가."""
        conn = _make_mock_conn(
            fetchrow_result=[
                None,  # consume UPDATE returns None
                _fake_record(
                    state="REQUESTED", used_at=None, expires_at=_now() + timedelta(minutes=55)
                ),
            ]
        )
        with pytest.raises(BreakGlassInvalidStateError, match="state=REQUESTED"):
            await consume(conn, grant_id=uuid4(), admin_id=uuid4())

    @pytest.mark.asyncio
    async def test_nonexistent_grant_raises_error(self):
        """존재하지 않는 grant_id로 소비 시도."""
        conn = _make_mock_conn(fetchrow_result=[None, None])
        with pytest.raises(BreakGlassInvalidStateError, match="존재하지 않습니다"):
            await consume(conn, grant_id=uuid4(), admin_id=uuid4())


# ── failure injection tests (≥1 required) ────────────────────────────────────


class TestFailureInjection:
    """의존성 예외 유발 테스트 — monkeypatch로 DB 예외를 유발."""

    @pytest.mark.asyncio
    async def test_request_grant_db_failure_raises_runtime_error(self):
        """DB 연결 손실 시 RuntimeError가 상위 레이어로 전파된다."""
        conn = _make_mock_conn(raise_on_fetchrow=True)
        with pytest.raises(RuntimeError, match="DB connection lost"):
            await request_grant(
                conn,
                requester_id=uuid4(),
                requester_mfa_verified_at=_now(),
                scope="tenant_read",
                reason="test",
                ttl_minutes=30,
            )

    @pytest.mark.asyncio
    async def test_approve_grant_db_failure_raises_runtime_error(self):
        """승인 중 DB 예외는 그대로 전파된다."""
        conn = _make_mock_conn(raise_on_fetchrow=True)
        checker = MagicMock()
        with pytest.raises(RuntimeError, match="DB connection lost"):
            await approve_grant(
                conn,
                grant_id=uuid4(),
                approver_id=uuid4(),
                approver_mfa_verified_at=_now(),
                check_segregation_of_duty=checker,
            )


# ── performance assertion (ADR-2026-09-09-C Decision 1 budget) ───────────────


class TestPerformance:
    """성능 단언 — ADR-2026-09-09-C Decision 1 예산 테이블 기준:
    MFA 검증(순수 함수) p95 ≤ 0.1 ms.
    """

    def test_mfa_step_up_fresh_within_budget(self):
        """mfa_step_up_fresh() 호출 10만 건이 1초 미만이어야 한다
        (p95 ≤ 0.1 ms 가정)."""
        now = _now()
        iterations = 100_000
        # Warm-up
        mfa_step_up_fresh(now)
        mfa_step_up_fresh(None)

        import time

        start = time.monotonic()
        for _ in range(iterations):
            mfa_step_up_fresh(now)
            mfa_step_up_fresh(None)
        elapsed_ms = (time.monotonic() - start) * 1000

        # 10만 건 * 2 호출 = 20만 호출, budget: 50ms 미만
        assert elapsed_ms < 50, (
            f"mfa_step_up_fresh perf budget exceeded: "
            f"{elapsed_ms:.1f}ms for {iterations * 2} calls (budget: 50ms)"
        )
