"""FA-18 write gate tests: write_available() blocks writes during recovery window.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18.
Tests that get_writable_connection() honours the write gate introduced to fix
QA 21795 -- writes must be blocked for recovery_window_seconds after failover.
"""

from __future__ import annotations

import pytest

from src.core.db.failover import (
    BackoffPolicy,
    FailoverConnectionManager,
    FailoverExhaustedError,
    HostEndpoint,
)


class _WritableConn:
    """A connection that reports writable."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.closed = False

    async def is_read_only(self) -> bool:
        return False

    async def close(self) -> None:
        self.closed = True


class _MutableClock:
    """A clock that can be advanced in seconds by the test."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


async def test_gate_blocks_write_during_recovery_window() -> None:
    """Negative test (DoD): after failover, writes blocked for
    recovery_window_seconds. Entering before the window expires must
    raise FailoverExhaustedError and must NOT return a replica connection.

    recovery_window_seconds=120, ceiling=60. Failover at t=100.
    Entry at t=110 -- only 10s elapsed, window=120s, ceiling=60s ->
    waits until t=170 (100+60=ceiling deadline) which is past ceiling -> raises.
    """
    clock = _MutableClock(start=100.0)
    replica = _WritableConn("replica")
    sleep_count = 0
    sleep_durations: list[float] = []

    async def connect(host: str) -> _WritableConn:
        return replica

    async def counting_sleep(duration: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        sleep_durations.append(duration)
        clock.advance(duration)

    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        backoff=BackoffPolicy(
            initial_seconds=0.01,
            max_seconds=0.01,
            multiplier=2.0,
            ceiling_seconds=60.0,
        ),
        clock=clock,
        sleep=counting_sleep,
        recovery_window_seconds=120.0,
    )

    # Simulate: we were on "primary", failover to "replica" at t=100
    mgr._current_host = "primary"
    mgr._last_failover_at = 100.0
    clock._t = 110.0  # we are at t=110, 10s after failover

    # Now try to get a writable connection -- should be blocked by gate
    with pytest.raises(FailoverExhaustedError) as exc_info:
        await mgr.get_writable_connection()

    assert "write gate still closed past ceiling" in str(exc_info.value)
    # Must have slept at least once (gate was waiting)
    assert sleep_count >= 1


async def test_gate_allows_write_after_recovery_window() -> None:
    """Pass-through test (DoD): recovery_window_seconds=60. Failover at t=100.
    Entry at t=130 -- 30s elapsed, window=60s. Wait until window opens at t=160.
    Must return the replica and clock must be >= 160 at return.
    """
    clock = _MutableClock(start=100.0)
    replica = _WritableConn("replica")
    sleep_count = 0

    async def connect(host: str) -> _WritableConn:
        return replica

    async def counting_sleep(duration: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        clock.advance(duration)

    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        backoff=BackoffPolicy(
            initial_seconds=0.01,
            max_seconds=0.01,
            multiplier=2.0,
            ceiling_seconds=60.0,
        ),
        clock=clock,
        sleep=counting_sleep,
        recovery_window_seconds=60.0,
    )

    # Simulate: we were on "primary", failover to "replica" at t=100
    mgr._current_host = "primary"
    mgr._last_failover_at = 100.0
    clock._t = 130.0  # 30s after failover

    # Should succeed after waiting for recovery window
    conn = await mgr.get_writable_connection()
    assert conn is replica
    # Clock should have advanced to at least t=160 (100+60 recovery window)
    assert clock._t >= 160.0
    # Must have slept at least once
    assert sleep_count >= 1


async def test_no_gate_when_no_failover() -> None:
    """Regression: if no failover has occurred (_last_failover_at is None),
    write_available() is always True, so sleep is never called."""
    clock = _MutableClock(start=0.0)
    primary = _WritableConn("primary")
    sleep_count = 0

    async def connect(host: str) -> _WritableConn:
        return primary

    async def counting_sleep(_: float) -> None:
        nonlocal sleep_count
        sleep_count += 1

    mgr = FailoverConnectionManager(
        [HostEndpoint("primary")],
        connect=connect,
        clock=clock,
        sleep=counting_sleep,
        recovery_window_seconds=60.0,
    )

    conn = await mgr.get_writable_connection()
    assert conn is primary
    # sleep must NOT have been called (no failover -> gate always open)
    assert sleep_count == 0
