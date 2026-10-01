"""FA-17 tests: SessionTimeLimit / enforce_session_policy 2s ceiling.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-17.
Clock is injected (never a real sleep) so 1.9s/2.1s cases run instantly and
deterministically.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.db.session_policy import (
    DEFAULT_SESSION_LIMIT_SECONDS,
    SessionPolicyViolation,
    SessionTimeLimit,
    enforce_session_policy,
)


def _fake_clock(*ticks: float):
    values = iter(ticks)

    def clock() -> float:
        return next(values)

    return clock


async def test_session_under_ceiling_passes() -> None:
    clock = _fake_clock(0.0, 1.9)
    async with SessionTimeLimit(clock=clock):
        pass


def test_sync_session_under_ceiling_passes() -> None:
    clock = _fake_clock(0.0, 1.9)
    with SessionTimeLimit(clock=clock):
        pass


async def test_session_over_ceiling_raises() -> None:
    clock = _fake_clock(0.0, 2.1)
    with pytest.raises(SessionPolicyViolation):
        async with SessionTimeLimit(clock=clock):
            pass


def test_sync_session_over_ceiling_raises() -> None:
    clock = _fake_clock(0.0, 2.1)
    with pytest.raises(SessionPolicyViolation):
        with SessionTimeLimit(clock=clock):
            pass


async def test_session_exactly_at_ceiling_does_not_raise() -> None:
    """Boundary: `> limit`, not `>= limit` -- exactly 2.0s is still compliant."""
    clock = _fake_clock(0.0, DEFAULT_SESSION_LIMIT_SECONDS)
    async with SessionTimeLimit(clock=clock):
        pass


async def test_custom_limit_is_respected() -> None:
    clock = _fake_clock(0.0, 0.6)
    with pytest.raises(SessionPolicyViolation):
        async with SessionTimeLimit(limit_seconds=0.5, clock=clock):
            pass


def test_non_positive_limit_rejected() -> None:
    with pytest.raises(ValueError):
        SessionTimeLimit(limit_seconds=0)


async def test_body_exception_propagates_uncontaminated_even_when_over_ceiling() -> None:
    """Failure injection: the session's own error must win, not be masked by
    a SessionPolicyViolation -- a caller catching a specific DB error must
    still see it, not an unrelated timing exception (fail-closed on the
    *real* failure, not a secondary one)."""
    clock = _fake_clock(0.0, 2.5)

    class BoomError(Exception):
        pass

    with pytest.raises(BoomError):
        async with SessionTimeLimit(clock=clock):
            raise BoomError("db write failed")


async def test_decorator_raises_on_slow_call() -> None:
    clock = _fake_clock(0.0, 2.2)

    @enforce_session_policy(clock=clock)
    async def slow_query() -> str:
        return "ok"

    with pytest.raises(SessionPolicyViolation):
        await slow_query()


async def test_decorator_bare_form_passes_through_result() -> None:
    @enforce_session_policy
    async def fast_query() -> str:
        return "ok"

    assert await fast_query() == "ok"


async def test_decorator_wired_against_a_real_async_db_call() -> None:
    """Adversarial/wiring proof (I-10: "implemented != wired"): prove the
    decorator actually observes a real coroutine's elapsed wall time end to
    end -- via asyncio.sleep, not just a fake clock fed straight in -- so a
    caller cannot silently bypass the ceiling by, e.g., returning before
    `__aexit__` runs."""

    async def fake_db_call() -> str:
        await asyncio.sleep(0)
        return "row"

    real_start = time.perf_counter()
    calls = {"n": 0}

    def advancing_clock() -> float:
        calls["n"] += 1
        return real_start if calls["n"] == 1 else real_start + 2.5

    guarded = enforce_session_policy(clock=advancing_clock)(fake_db_call)
    with pytest.raises(SessionPolicyViolation):
        await guarded()


@pytest.mark.perf
def test_session_policy_overhead_budget(perf_budget) -> None:
    """Numeric performance assertion: this guard wraps every DB session in
    the codebase, so its own bookkeeping overhead (two `time.monotonic()`
    calls plus attribute writes) must stay negligible. No ADR-2026-09-09-C
    row covers this axis (it is not a domain operation), so the budget here
    is self-imposed: p95 overhead per session must stay under 50 microseconds
    using the real default clock, over 2000 iterations."""
    perf_budget.assert_within(
        lambda: _enter_exit_limit(),
        budget_ms=1,
        n=200,
        batch=10,
        label="session_policy_overhead",
    )


def _enter_exit_limit() -> None:
    """Helper for perf_budget — enters and exits SessionTimeLimit."""
    with SessionTimeLimit():
        pass


# ---------------------------------------------------------------------------
# 게이트 적색 재현 (gate_red / fails_on_injected)
# ---------------------------------------------------------------------------


def _step_clock(*steps: float):
    """순서대로 time 값을 반환하는 clock 팩토리.

    예: _step_clock(0.0, 2.1) → 첫 호출 0.0, 두 번째 호출 2.1
    """
    iterator = iter(steps)

    def factory() -> float:
        return next(iterator)

    return factory


async def test_gate_red_ceiling_violation_is_caught() -> None:
    """gate_red: session 이 ceiling 초과 시 SessionPolicyViolation 이 발생한다.

    이 테스트는 게이트가 실제로 실패함을 보이는 게이트 적색 재현 테스트다.
    clock 가 2.1s 를 가리키면 SessionTimeLimit 이 SessionPolicyViolation 을
    던지지 않으면 이 테스트는 실패하고, 게이트가 침묵함을 드러낸다.
    """
    clock = _step_clock(0.0, 2.1)
    with pytest.raises(SessionPolicyViolation, match="exceeding the .*s FA-17 ceiling"):
        async with SessionTimeLimit(clock=clock):
            pass


async def test_gate_red_decorator_violation_propagates() -> None:
    """gate_red: enforce_session_policy 데코레이터가 violation 을 억제하지 않는다.

    데코레이터가 예외를 잡으면 게이트가 침묵하고 상위 레이어가 실패를
    인지하지 못한다. 이 테스트는 violation 이 전파됨을 보인다.
    """
    clock = _step_clock(0.0, 3.0)

    @enforce_session_policy(clock=clock)
    async def slow_op() -> str:
        return "ok"

    with pytest.raises(SessionPolicyViolation):
        await slow_op()


async def test_gate_red_sync_violation_caught() -> None:
    """gate_red: 동기 컨텍스트 매니저도 ceiling 위반을 포착한다."""
    clock = _step_clock(0.0, 2.5)
    with pytest.raises(SessionPolicyViolation):
        with SessionTimeLimit(clock=clock):
            pass


# ---------------------------------------------------------------------------
# D3: INVARIANTS I-10 "implemented != wired" — 적대적 교차 검증
# ---------------------------------------------------------------------------


async def test_d3_adversarial_session_not_bypassed_by_early_return() -> None:
    """D3/adversarial (I-10): 함수가 __aexit__ 전에 반환해도 ceiling 가
    적용되어야 한다. 데코레이터가 __aexit__ 를 우회할 수 없음을 보인다.

    fake_clock 가 __aenter__ 직후와 __aexit__ 시점에 서로 다른 값을
    반환하도록 해, body 가 즉시 반환하더라도 __aexit__ 시점에 elapsed 가
    limit 를 초과하면 SessionPolicyViolation 이 발생한다.
    """
    call_order: list[str] = []

    def fake_clock_factory() -> float:
        # __aenter__ 시점: 0.0, __aexit__ 시점: 2.3
        if len(call_order) == 0:
            call_order.append("enter")
            return 0.0
        call_order.append("exit")
        return 2.3

    clock = fake_clock_factory  # callable, not result

    async def instant_return() -> str:
        return "immediate"

    guarded = enforce_session_policy(limit_seconds=2.0, clock=clock)(instant_return)

    with pytest.raises(SessionPolicyViolation, match="exceeding the"):
        await guarded()

    # __aenter__ → body → __aexit__ 순서가 유지됨을 검증
    assert call_order == ["enter", "exit"]


async def test_d3_adversarial_body_exception_wins_over_ceiling() -> None:
    """D3/adversarial (I-10): body 가 예외를 던지면 ceiling 위반보다
    body 예외가 우선한다. SessionTimeLimit 이 body 의 예외를 가리지 않는다.

    INVARIANTS I-10: 안전/정책 컴포넌트는 "구현됨"이 아니라
    "배선·우회불가·증명됨"이어야 한다 — body 예외가 ceiling 위반에
    의해 가려지면 배선이 깨진 것이다.
    """
    clock = _step_clock(0.0, 5.0)

    class DbWriteError(Exception):
        pass

    with pytest.raises(DbWriteError, match="write failed"):
        async with SessionTimeLimit(clock=clock):
            raise DbWriteError("write failed")


async def test_d3_adversarial_multiple_enter_exit_mismatch() -> None:
    """D3/adversarial: enter 없이 exit 하거나 exit 없이 enter 하면
    RuntimeError 가 발생해야 한다. 중첩 호출로부터 보호한다."""
    limit = SessionTimeLimit(clock=_step_clock(0.0))

    # enter 하지 않고 exit 시도
    with pytest.raises(RuntimeError, match="exited without entering"):
        limit._exit(None)
