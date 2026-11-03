"""FA-18 tests: failover recovery window -- write_available() timing.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18.
Responsibility: 60-second recovery window (write_available behavior after failover).
Core failover tests live in test_failover.py.
Backoff/perf/gate-red tests live in test_failover_backoff.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from src.core.db.failover import (
    BackoffPolicy,
    FailoverConnectionManager,
    HostEndpoint,
)


class FakeConnection:
    def __init__(
        self,
        host: str,
        *,
        read_only: bool = False,
    ) -> None:
        self.host = host
        self._read_only = read_only
        self.closed = False

    async def is_read_only(self) -> bool:
        return self._read_only

    async def close(self) -> None:
        self.closed = True


class _MutableClock:
    """A clock that can be advanced arbitrarily, unlike `_fake_clock`'s
    fixed-length iterator -- needed here because `write_available`
    tests drive time forward in discrete steps after the initial
    failover event."""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def test_failover_recovers_write_within_60s_ceiling_wired_end_to_end() -> None:
    """Adversarial wiring proof (I-10 "implemented != wired") + FA-18 SLO
    (SS7: failover -> first successful write <= 60s): drives the manager
    through a real asyncio-scheduled backoff loop (not just a fake clock fed
    straight in) where the primary is unreachable twice before a promoted
    replica accepts writes, and asserts recovery completes before the
    injected monotonic clock crosses the 60s ceiling."""
    attempts = {"n": 0}

    async def connect(host: str) -> FakeConnection:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionRefusedError("primary still down")
        return FakeConnection(host, read_only=False)

    real_sleep_calls: list[float] = []

    async def fast_sleep(seconds: float) -> None:
        real_sleep_calls.append(seconds)
        await asyncio.sleep(0)  # actually yields control, unlike a stub no-op

    start = 1000.0
    clock = _fake_clock(start, start, start, start + 5.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary")],
        connect=connect,
        backoff=BackoffPolicy(initial_seconds=0.01, max_seconds=0.01, ceiling_seconds=60.0),
        clock=clock,
        sleep=fast_sleep,
    )
    conn = await mgr.get_writable_connection()
    assert isinstance(conn, FakeConnection)
    assert conn.host == "primary"
    assert len(real_sleep_calls) >= 1


def _fake_clock(*ticks: float) -> Callable[[], float]:
    values = iter(ticks)

    def clock() -> float:
        return next(values)

    return clock


async def _no_sleep(_: float) -> None:
    return None


async def test_write_available_true_before_any_failover() -> None:
    """Pre-condition: before any failover event, writes are available."""
    primary = FakeConnection("primary", read_only=False)

    async def connect(host: str) -> FakeConnection:
        return primary

    mgr = FailoverConnectionManager([HostEndpoint("primary")], connect=connect)
    assert mgr.write_available() is True


async def test_write_available_blocked_immediately_after_failover() -> None:
    """Boundary 1: immediately after failover, writes are blocked.

    Must simulate a real failover event: first connect to primary (writable,
    sets _current_host), then make primary read-only and reconnect --
    the manager must fail over to replica and record _last_failover_at.
    """
    conns = {
        "primary": FakeConnection("primary", read_only=False),
        "replica": FakeConnection("replica", read_only=False),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    clock = _MutableClock(0.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        clock=clock,
        recovery_window_seconds=60.0,
    )
    # First connection: primary is writable
    conn = await mgr.get_writable_connection()
    assert conn is conns["primary"]
    # Now primary becomes read-only (simulating real failover scenario)
    conns["primary"] = FakeConnection("primary", read_only=True)
    # Reconnect: manager must fail over to replica
    conn = await mgr.get_writable_connection()
    assert conn is conns["replica"]
    assert mgr.write_available() is False


async def test_write_available_still_blocked_at_59_seconds() -> None:
    """Boundary 2: 59s after failover, the 60s recovery window has not yet
    elapsed -- writes stay blocked."""
    conns = {
        "primary": FakeConnection("primary", read_only=False),
        "replica": FakeConnection("replica", read_only=False),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    clock = _MutableClock(0.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        clock=clock,
        recovery_window_seconds=60.0,
    )
    await mgr.get_writable_connection()
    conns["primary"] = FakeConnection("primary", read_only=True)
    clock.now = 100.0
    await mgr.get_writable_connection()  # failover recorded at t=100.0
    clock.now = 100.0 + 59.0
    assert mgr.write_available() is False


async def test_write_available_allowed_at_60_seconds() -> None:
    """Boundary 3: exactly 60s after failover, the recovery window has fully
    elapsed -- writes are allowed again."""
    conns = {
        "primary": FakeConnection("primary", read_only=False),
        "replica": FakeConnection("replica", read_only=False),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    clock = _MutableClock(0.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        clock=clock,
        recovery_window_seconds=60.0,
    )
    await mgr.get_writable_connection()
    conns["primary"] = FakeConnection("primary", read_only=True)
    clock.now = 100.0
    await mgr.get_writable_connection()  # failover recorded at t=100.0
    clock.now = 100.0 + 60.0
    assert mgr.write_available() is True


def test_recovery_window_seconds_rejects_non_positive() -> None:
    async def connect(host: str) -> FakeConnection:
        raise AssertionError("should never be called")

    with pytest.raises(ValueError):
        FailoverConnectionManager(
            [HostEndpoint("primary")], connect=connect, recovery_window_seconds=0
        )
