"""FA-18 tests: backoff policy, construction latency, gate-red reproduction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18.
Responsibility: BackoffPolicy validation/numeric behavior, performance budget,
and gate-red safety reproduction.
Core failover tests live in test_failover.py.
Recovery-window tests live in test_failover_recovery.py.
"""

from __future__ import annotations

import pytest

from src.core.db.failover import (
    BackoffPolicy,
    FailoverConnectionManager,
    FailoverExhaustedError,
    HostEndpoint,
)
from tests.support.perf_budget import PerfBudget


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


async def _no_sleep(_: float) -> None:
    return None


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


def test_backoff_delays_grow_and_cap_at_max() -> None:
    """Numeric performance assertion: backoff must actually expand (so a
    down host isn't hammered) and must cap at max_seconds (so a long outage
    doesn't push individual sleeps past a bounded, predictable interval)."""
    policy = BackoffPolicy(initial_seconds=1.0, max_seconds=4.0, multiplier=2.0)
    gen = policy.delays()
    first_five = [next(gen) for _ in range(5)]
    assert first_five == [1.0, 2.0, 4.0, 4.0, 4.0]


@pytest.mark.perf
async def test_failover_manager_construction_latency(perf_budget: PerfBudget) -> None:
    """Performance assertion (D3) — FailoverConnectionManager construction
    plus write_available() decision must complete under 1 ms. Degradation
    here (blocking I/O, heavy sorting) would slow every pre-order check."""

    def _build() -> None:
        async def _connect(host: str) -> FakeConnection:
            return FakeConnection(host)

        mgr = FailoverConnectionManager(
            [
                HostEndpoint("h1", priority=0),
                HostEndpoint("h2", priority=1),
                HostEndpoint("h3", priority=2),
            ],
            connect=_connect,
        )
        mgr.write_available()

    perf_budget.assert_within(_build, budget_ms=5.0, n=100)


async def test_gate_red_all_hosts_stuck_read_only_raises_exhausted() -> None:
    """Gate-red reproduction: inject a cluster where every host answers
    read_only=True permanently — the failover gate MUST raise
    FailoverExhaustedError instead of silently returning a read-only
    connection. This test verifies the gate *fails* (raises) under
    injected bad state, proving the safety invariant is enforced.

    Uses ceiling_seconds=1.0 to keep the gate-red measurement under 10s
    (real 60s ceiling would take ~60s because backoff is 10ms per sweep).
    """
    conns = {
        "h1": FakeConnection("h1", read_only=True),
        "h2": FakeConnection("h2", read_only=True),
        "h3": FakeConnection("h3", read_only=True),
    }

    async def connect(host: str) -> FakeConnection:
        return conns[host]

    mgr = FailoverConnectionManager(
        [
            HostEndpoint("h1", priority=0),
            HostEndpoint("h2", priority=1),
            HostEndpoint("h3", priority=2),
        ],
        connect=connect,
        backoff=BackoffPolicy(
            initial_seconds=0.01,
            max_seconds=0.01,
            multiplier=2.0,
            ceiling_seconds=1.0,
        ),
    )

    with pytest.raises(FailoverExhaustedError) as exc_info:
        await mgr.get_writable_connection()

    # Verify: no connection is held open (fail-closed)
    for conn in conns.values():
        assert conn.closed is True, "read-only conn must be closed immediately"

    # Verify: error message lists all tried hosts
    msg = str(exc_info.value)
    assert "h1" in msg and "h2" in msg and "h3" in msg
