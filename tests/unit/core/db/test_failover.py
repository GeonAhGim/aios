"""FA-18 tests: multi-host failover -- read-only detection, backoff reconnect.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18.
All ports (connect/clock/sleep) are faked -- no real Postgres cluster --
so failover/backoff/ceiling behavior is deterministic and instant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from src.core.db.failover import (
    BackoffPolicy,
    FailoverConnectionManager,
    FailoverExhaustedError,
    HostEndpoint,
)


class FakeConnection:
    def __init__(
        self,
        host: str,
        *,
        read_only: bool = False,
        raise_on_check: Exception | None = None,
    ) -> None:
        self.host = host
        self._read_only = read_only
        self._raise_on_check = raise_on_check
        self.closed = False

    async def is_read_only(self) -> bool:
        if self._raise_on_check is not None:
            raise self._raise_on_check
        return self._read_only

    async def close(self) -> None:
        self.closed = True


def _fake_clock(*ticks: float) -> Callable[[], float]:
    values = iter(ticks)

    def clock() -> float:
        return next(values)

    return clock


async def _no_sleep(_: float) -> None:
    return None


async def test_primary_writable_returns_immediately() -> None:
    primary = FakeConnection("primary")

    async def connect(host: str) -> FakeConnection:
        assert host == "primary"
        return primary

    mgr = FailoverConnectionManager([HostEndpoint("primary")], connect=connect)
    conn = await mgr.get_writable_connection()
    assert conn is primary
    assert mgr.current_host == "primary"


async def test_read_only_primary_fails_over_to_replica() -> None:
    conns = {
        "primary": FakeConnection("primary", read_only=True),
        "replica": FakeConnection("replica", read_only=False),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
    )
    conn = await mgr.get_writable_connection()
    assert conn is conns["replica"]
    assert mgr.current_host == "replica"
    assert conns["primary"].closed is True  # fail-closed: read-only conn is not held open


async def test_connect_failure_on_primary_falls_through_to_replica() -> None:
    replica = FakeConnection("replica")

    async def connect(host: str) -> FakeConnection:
        if host == "primary":
            raise ConnectionRefusedError("primary unreachable")
        return replica

    mgr = FailoverConnectionManager(
        [HostEndpoint("primary"), HostEndpoint("replica", priority=1)],
        connect=connect,
    )
    conn = await mgr.get_writable_connection()
    assert conn is replica
    assert mgr.current_host == "replica"


async def test_is_read_only_check_raising_is_treated_as_failure_and_conn_closed() -> None:
    """Failure injection: a host that connects but whose read-only probe
    itself raises (e.g. driver error mid-query) must not be trusted as
    writable -- it is skipped and its connection closed, not returned."""
    bad = FakeConnection("bad", raise_on_check=RuntimeError("probe failed"))
    good = FakeConnection("good", read_only=False)

    async def connect(host: str) -> FakeConnection:
        return {"bad": bad, "good": good}[host]

    mgr = FailoverConnectionManager(
        [HostEndpoint("bad", priority=0), HostEndpoint("good", priority=1)],
        connect=connect,
    )
    conn = await mgr.get_writable_connection()
    assert conn is good
    assert bad.closed is True


async def test_all_hosts_read_only_exhausts_within_ceiling_and_raises() -> None:
    """Negative: if every host stays read-only for the whole ceiling window,
    the manager fails closed instead of retrying forever in a hot write
    path."""

    async def connect(host: str) -> FakeConnection:
        return FakeConnection(host, read_only=True)

    clock = _fake_clock(0.0, 1.0, 2.0, 3.0, 70.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("only")],
        connect=connect,
        backoff=BackoffPolicy(initial_seconds=0.01, max_seconds=0.01, ceiling_seconds=60.0),
        clock=clock,
        sleep=_no_sleep,
    )
    with pytest.raises(FailoverExhaustedError):
        await mgr.get_writable_connection()


async def test_all_hosts_unreachable_exhausts_and_raises_with_last_error_chained() -> None:
    async def connect(host: str) -> FakeConnection:
        raise TimeoutError(f"{host} timed out")

    clock = _fake_clock(0.0, 61.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary")],
        connect=connect,
        backoff=BackoffPolicy(ceiling_seconds=60.0),
        clock=clock,
        sleep=_no_sleep,
    )
    with pytest.raises(FailoverExhaustedError) as excinfo:
        await mgr.get_writable_connection()
    assert isinstance(excinfo.value.__cause__, TimeoutError)


async def test_empty_host_list_rejected() -> None:
    async def connect(host: str) -> FakeConnection:
        raise AssertionError("should never be called")

    with pytest.raises(ValueError):
        FailoverConnectionManager([], connect=connect)


def test_backoff_policy_rejects_non_positive_seconds() -> None:
    with pytest.raises(ValueError):
        BackoffPolicy(initial_seconds=0)
    with pytest.raises(ValueError):
        BackoffPolicy(max_seconds=-1)
    with pytest.raises(ValueError):
        BackoffPolicy(ceiling_seconds=0)


def test_backoff_policy_rejects_non_expanding_multiplier() -> None:
    with pytest.raises(ValueError):
        BackoffPolicy(multiplier=1.0)


async def test_hosts_are_tried_in_priority_order_regardless_of_input_order() -> None:
    seen: list[str] = []

    async def connect(host: str) -> FakeConnection:
        seen.append(host)
        return FakeConnection(host, read_only=True)

    clock = _fake_clock(0.0, 61.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("replica", priority=1), HostEndpoint("primary", priority=0)],
        connect=connect,
        backoff=BackoffPolicy(ceiling_seconds=60.0),
        clock=clock,
        sleep=_no_sleep,
    )
    with pytest.raises(FailoverExhaustedError):
        await mgr.get_writable_connection()
    assert seen == ["primary", "replica"]


async def test_close_closes_current_connection_and_clears_state() -> None:
    primary = FakeConnection("primary")

    async def connect(host: str) -> FakeConnection:
        return primary

    mgr = FailoverConnectionManager([HostEndpoint("primary")], connect=connect)
    await mgr.get_writable_connection()
    await mgr.close()
    assert primary.closed is True
    assert mgr.current_host is None


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


class _MutableClock:
    """A clock that can be advanced arbitrarily, unlike `_fake_clock`'s
    fixed-length iterator -- needed here because `write_available()` polls
    the clock independently of `get_writable_connection()`'s own reads."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def test_write_available_true_before_any_failover() -> None:
    """A manager that has never switched hosts always allows writes."""
    primary = FakeConnection("primary")

    async def connect(host: str) -> FakeConnection:
        return primary

    clock = _MutableClock(0.0)
    mgr = FailoverConnectionManager([HostEndpoint("primary")], connect=connect, clock=clock)
    await mgr.get_writable_connection()
    assert mgr.write_available() is True


async def test_write_available_blocked_immediately_after_failover() -> None:
    """Boundary 1: right after a host switch, writes are blocked -- a
    freshly promoted replica may still be settling."""
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
    )
    await mgr.get_writable_connection()
    assert mgr.write_available() is True  # first connect is not a failover

    conns["primary"] = FakeConnection("primary", read_only=True)
    await mgr.get_writable_connection()  # switches primary -> replica: a failover
    assert mgr.current_host == "replica"
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


def test_backoff_delays_grow_and_cap_at_max() -> None:
    """Numeric performance assertion: backoff must actually expand (so a
    down host isn't hammered) and must cap at max_seconds (so a long outage
    doesn't push individual sleeps past a bounded, predictable interval)."""
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=4.0, multiplier=2.0)
    gen = policy.delays()
    first_five = [next(gen) for _ in range(5)]
    assert first_five == [1.0, 2.0, 4.0, 4.0, 4.0]
