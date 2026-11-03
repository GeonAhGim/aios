"""FA-18 tests: multi-host failover -- read-only detection, backoff reconnect.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18.
All ports (connect/clock/sleep) are faked -- no real Postgres cluster --
so failover/backoff/ceiling behavior is deterministic and instant.

Responsibility: core failover (connection + read-only detection).
Recovery-window tests live in test_failover_recovery.py.
Backoff/perf/gate-red tests live in test_failover_backoff.py.
"""

from __future__ import annotations

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
    conns = {
        "primary": FakeConnection("primary", read_only=True),
        "replica": FakeConnection("replica", read_only=True),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    clock = _fake_clock(0.0, 61.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary", priority=0), HostEndpoint("replica", priority=1)],
        connect=connect,
        backoff=BackoffPolicy(ceiling_seconds=60.0),
        clock=clock,
        sleep=_no_sleep,
    )
    with pytest.raises(FailoverExhaustedError) as excinfo:
        await mgr.get_writable_connection()
    # When all hosts are read-only (not connection failures), __cause__
    # is None because read-only hosts don't set last_error -- the
    # exhausted error is raised without a chained cause.
    assert "primary" in str(excinfo.value) and "replica" in str(excinfo.value)


async def test_all_hosts_unreachable_exhausts_and_raises_with_last_error_chained() -> None:
    async def connect(host: str) -> FakeConnection:
        raise ConnectionRefusedError(f"{host} refused")

    clock = _fake_clock(0.0, 61.0)
    mgr = FailoverConnectionManager(
        [HostEndpoint("primary"), HostEndpoint("replica")],
        connect=connect,
        backoff=BackoffPolicy(ceiling_seconds=60.0),
        clock=clock,
        sleep=_no_sleep,
    )
    with pytest.raises(FailoverExhaustedError) as excinfo:
        await mgr.get_writable_connection()
    assert isinstance(excinfo.value.__cause__, ConnectionRefusedError)


async def test_empty_host_list_rejected() -> None:
    async def connect(host: str) -> FakeConnection:
        raise AssertionError("should never be called")

    with pytest.raises(ValueError):
        FailoverConnectionManager([], connect=connect)


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
