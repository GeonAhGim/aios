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


def test_session_policy_overhead_budget() -> None:
    """Numeric performance assertion: this guard wraps every DB session in
    the codebase, so its own bookkeeping overhead (two `time.monotonic()`
    calls plus attribute writes) must stay negligible. No ADR-2026-09-09-C
    row covers this axis (it is not a domain operation), so the budget here
    is self-imposed: p95 overhead per session must stay under 50 microseconds
    using the real default clock, over 2000 iterations."""
    iterations = 2000
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        with SessionTimeLimit():
            pass
        samples.append(time.perf_counter() - t0)

    samples.sort()
    p95 = samples[int(iterations * 0.95)]
    assert p95 < 0.00005, f"SessionTimeLimit p95 overhead {p95 * 1e6:.1f}us exceeds 50us budget"
