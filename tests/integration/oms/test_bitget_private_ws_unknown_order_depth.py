"""L4-20 `bitget/private_ws_mixin.py` -- unknown-order-id path depth (D3).

Split from test_bitget_private_ws_to_inbox.py (file would exceed the 800-line
ratchet). Shared helpers are imported from that module unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.private_ws_mixin import (
    parse_private_order_row,
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.test_bitget_private_ws_to_inbox import (
    _envelope,
    _FakeConnection,
    _never,
    _no_rest_calls_adapter,
    _order_row,
    _StopTest,
    _TrackingConnectCtx,
)
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


def _unknown_order_event(**overrides):
    row = _order_row(
        order_id=f"ex-does-not-exist-{uuid4().hex}",
        client_id=f"cid-does-not-exist-{uuid4().hex}",
    )
    row.update(overrides)
    return parse_private_order_row(row, received_at=datetime.now(UTC))


@pytest.mark.parametrize("override", [{"tradeId": "trade-A"}, {"tradeId": "trade-B"}])
def test_unknown_order_event_ids_are_deterministic_and_input_sensitive(override):
    """DEEPEN-DEPTH negative -- unknown-order events keep a stable
    provider_event_id for identical input and a different one when the
    tradeId changes (no accidental collapse into one inbox row)."""
    base_row = _order_row(order_id="ex-x", client_id="cid-x", trade_id="trade-x")
    now = datetime.now(UTC)
    ev1 = parse_private_order_row(dict(base_row), received_at=now)
    ev2 = parse_private_order_row(dict(base_row), received_at=now)
    ev3 = parse_private_order_row({**base_row, **override}, received_at=now)
    assert ev1.provider_event_id == ev2.provider_event_id
    assert ev3.provider_event_id != ev1.provider_event_id


async def test_unknown_order_replay_is_deduplicated_and_never_creates_fill(pool):
    """DEEPEN-DEPTH negative -- the same unknown-order trade delivered twice
    yields one IGNORED inbox row and zero fills (replay-safe, fail-closed)."""
    exchange_order_id = f"ex-does-not-exist-{uuid4().hex}"
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(order_id=exchange_order_id, client_id=f"cid-{uuid4().hex}", trade_id=trade_id)
    connection = _FakeConnection(
        [_envelope([row]), _envelope([row])], raise_after=ConnectionClosed(None, None)
    )
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=connect_fn,
            ping_sleep_fn=_never,
        )
    async with pool.acquire() as conn:
        inbox_rows = await conn.fetchval(
            "SELECT count(*) FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
        fills_count = await conn.fetchval(
            "SELECT count(*) FROM fills WHERE exchange_order_id = $1", exchange_order_id
        )
    assert inbox_rows == 1
    assert fills_count == 0


async def test_unknown_order_mixed_with_garbage_rows_still_lands_ignored(pool):
    """DEEPEN-DEPTH negative -- non-dict garbage around an unknown-order row
    is skipped without losing the unknown-order inbox record."""
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(
        order_id=f"ex-does-not-exist-{uuid4().hex}",
        client_id=f"cid-{uuid4().hex}",
        trade_id=trade_id,
    )
    connection = _FakeConnection(
        [_envelope(["junk", None, row])], raise_after=ConnectionClosed(None, None)
    )
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=connect_fn,
            ping_sleep_fn=_never,
        )
    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
    assert state == "IGNORED"


async def test_unknown_order_ingest_failure_propagates_not_swallowed():
    """DEEPEN-DEPTH failure injection -- an inbox failure while ingesting an
    unknown-order event must surface instead of being dropped silently."""

    class _BoomInbox(InboxProcessor):
        def __init__(self) -> None:
            pass

        async def ingest(self, ev):
            raise RuntimeError("injected unknown-order ingest failure")

    row = _order_row(order_id=f"ex-does-not-exist-{uuid4().hex}", client_id=f"cid-{uuid4().hex}")
    connection = _FakeConnection([_envelope([row])], hang=True)
    with pytest.raises(RuntimeError, match="injected unknown-order ingest failure"):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            _BoomInbox(),
            connect_fn=lambda url: _TrackingConnectCtx(connection),
            ping_sleep_fn=_never,
        )


# Local measurement (best-of-5 cpu): ~2-3 ms per 200 parses; 100 ms budget
# leaves >=30x headroom.
_UNKNOWN_ORDER_PARSE_BUDGET_MS = 100.0


def _parse_unknown_order_batch() -> None:
    now = datetime.now(UTC)
    for i in range(200):
        parse_private_order_row(
            _order_row(order_id=f"ex-none-{i}", client_id=f"cid-none-{i}", trade_id=f"t-{i}"),
            received_at=now,
        )


@pytest.mark.perf
def test_unknown_order_parse_within_budget(perf_budget):
    """DEEPEN-DEPTH perf -- best-of-N cpu budget for unknown-order parsing."""
    perf_budget.assert_within(
        _parse_unknown_order_batch,
        budget_ms=_UNKNOWN_ORDER_PARSE_BUDGET_MS,
        n=5,
        label="unknown_order_parse",
    )


@pytest.mark.perf
def test_unknown_order_parse_budget_goes_red_under_10x_virtual_delay(perf_budget):
    """DEEPEN-DEPTH gate-red repro -- 10x virtual delay must trip the budget."""
    from tests.support.perf_inject import virtual_delayed_sync

    slow = virtual_delayed_sync(
        _parse_unknown_order_batch, _UNKNOWN_ORDER_PARSE_BUDGET_MS * 10 / 1000
    )
    with pytest.raises(AssertionError, match="unknown_order_parse"):
        perf_budget.assert_within(
            slow, budget_ms=_UNKNOWN_ORDER_PARSE_BUDGET_MS, n=3, label="unknown_order_parse"
        )
