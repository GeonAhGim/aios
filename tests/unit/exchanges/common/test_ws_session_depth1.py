"""L4-19 `WsSession` depth-floor reinforcement (task-24890, DEEPEN-DEPTH PLT-42, D1 -> D2).

Gaps closed: failure injection 0, performance assertion 0, gate-red reproduction 0.
Reuses the fixtures of `test_ws_session.py` (no redefinition); that file is not modified.

Added tests:
- negative: connect_fn non-OSError propagates, sleep_fn failure surfaces, seq-less frames
  never trigger resync, handler failure propagates without a reconnect.
- failure injection: the reconnect backoff sleep fails mid-loop.
- performance: `PerfBudget.best_of_async` over a 500-frame dispatch.
  Measured locally (best of 5, wall_ms): ~1.2 ms. Budget 50 ms = 40x headroom.
  The virtual delay is booked in wall_ms (not cpu_ms), so the budget is on wall_ms.
- gate-red reproduction: a 10x virtual delay (`perf_inject.virtual_delayed_async`)
  on the handler pushes the same measurement past the budget, proving the gate catches it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async
from tests.unit.exchanges.common.test_ws_session import (
    SUB,
    _closed,
    _connect_sequence,
    _FakeConnection,
    _Harness,
    _no_sleep,
    _Stop,
)

_FRAME_COUNT = 500
_DISPATCH_BUDGET_MS = 50.0
_BASE_DELAY_S = 0.001  # normal per-frame handler cost; the injection is 10x of it


# ---------- negative ----------


async def test_connect_fn_runtime_error_propagates_without_retry():
    """negative — a non-OSError from connect_fn is not retried; it reaches the caller."""
    h = _Harness()
    calls = {"n": 0}

    def connect_fn(_url: str):
        calls["n"] += 1
        raise RuntimeError("setup failed")

    with pytest.raises(RuntimeError, match="setup failed"):
        await h.session(connect_fn).run([SUB], h.handler)

    assert calls["n"] == 1
    assert h.received == []


async def test_missing_seq_field_does_not_trigger_resync():
    """negative — frames without a seq skip gap detection and never cause a resync."""
    h = _Harness()
    frames = [json.dumps(f) for f in ({"seq": 1}, {"data": "no_seq"}, {"seq": 2})]
    connect_fn, _ = _connect_sequence([_FakeConnection(frames, raise_after=_closed())])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert len(h.received) == 3
    assert "resync" not in h.events
    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 0


async def test_handler_exception_propagates_without_reconnect():
    """negative — a handler bug is not swallowed as a connection drop (no reconnect)."""
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1})], raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn, _FakeConnection([])])

    async def bad_handler(_message: dict[str, Any]) -> None:
        raise ValueError("handler bug")

    with pytest.raises(ValueError, match="handler bug"):
        await h.session(connect_fn).run([SUB], bad_handler)

    assert calls["n"] == 1


# ---------- failure injection ----------


async def test_sleep_fn_failure_stops_reconnect_loop():
    """failure injection — the backoff sleep dies on the second reconnect; the error
    surfaces and no third connection is attempted."""
    h = _Harness()
    conns = [_FakeConnection([], raise_after=_closed()) for _ in range(3)]
    connect_fn, calls = _connect_sequence(conns)
    sleeps = {"n": 0}

    async def failing_sleep(_: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise OSError("sleep failed")

    with pytest.raises(OSError, match="sleep failed"):
        await h.session(connect_fn, sleep_fn=failing_sleep).run([SUB], h.handler)

    assert calls["n"] == 2


async def test_closed_connection_is_retried_not_propagated():
    """negative — ConnectionClosed is absorbed by the reconnect loop (only _Stop ends it)."""
    h = _Harness()
    conn = _FakeConnection([json.dumps({"seq": 1})], raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn, sleep_fn=_no_sleep).run([SUB], h.handler)

    assert calls["n"] == 2
    assert not issubclass(_Stop, ConnectionClosed)


# ---------- performance + gate-red reproduction ----------


async def _dispatch_frames(handler_delay_s: float = 0.0) -> int:
    """Run one session over `_FRAME_COUNT` frames; optionally wrap the handler with a
    virtual per-frame delay (no real sleeping)."""
    h = _Harness()
    frames = [json.dumps({"seq": s}) for s in range(1, _FRAME_COUNT + 1)]
    connect_fn, _ = _connect_sequence([_FakeConnection(frames, raise_after=_closed())])
    handler = h.handler
    if handler_delay_s > 0.0:
        handler = virtual_delayed_async(h.handler, handler_delay_s)
    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], handler)
    return len(h.received)


@pytest.mark.perf
async def test_dispatch_500_frames_within_budget():
    """perf — best-of-5 wall time to dispatch 500 frames stays under the budget.

    Measured locally: ~1.2 ms wall (best_of_async n=5, 3 runs 1.1-1.2). Budget 50 ms (40x headroom).
    """
    reset_virtual_totals()
    sample = await PerfBudget().best_of_async(_dispatch_frames, n=5, warmup=1)
    assert sample.result == _FRAME_COUNT
    assert sample.wall_ms < _DISPATCH_BUDGET_MS, (
        f"dispatch wall={sample.wall_ms:.3f}ms exceeds {_DISPATCH_BUDGET_MS}ms"
    )


@pytest.mark.perf
async def test_gate_red_when_handler_delay_injected_10x():
    """gate-red reproduction — a 10x virtual handler delay (10 ms/frame vs the 1 ms base)
    makes the same measurement exceed the budget, so the assertion above really catches it."""
    reset_virtual_totals()
    sample = await PerfBudget().best_of_async(
        lambda: _dispatch_frames(_BASE_DELAY_S * 10), n=5, warmup=1
    )
    assert sample.result == _FRAME_COUNT
    assert sample.wall_ms > _DISPATCH_BUDGET_MS, (
        f"injection not detected: wall={sample.wall_ms:.3f}ms <= {_DISPATCH_BUDGET_MS}ms"
    )
