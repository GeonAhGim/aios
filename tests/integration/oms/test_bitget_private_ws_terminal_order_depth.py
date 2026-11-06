"""L4-20 `bitget/private_ws_mixin.py` -- late fill for a terminal order (D3).

Split from test_bitget_private_ws_to_inbox.py (file is near the 800-line
ratchet). Shared helpers are imported from that module unchanged. Extends
`test_fill_event_for_terminal_order_does_not_resurrect_it` (CANCELLED only)
with every other terminal status, a mixed-batch case, failure injection,
a perf budget and its gate-red reproduction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.private_ws_mixin import (
    parse_private_order_row,
    subscribe_bitget_orders_to_inbox,
)
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.conftest import create_test_user
from tests.integration.oms.test_bitget_private_ws_to_inbox import (
    _envelope,
    _FakeConnection,
    _insert_order,
    _never,
    _no_rest_calls_adapter,
    _order_row,
    _StopTest,
    _TrackingConnectCtx,
)
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


async def _deliver(pool, rows: list[dict]) -> None:
    connection = _FakeConnection([_envelope(rows)], raise_after=ConnectionClosed(None, None))
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


async def _terminal_order(pool, status: str):
    user_id = await create_test_user(pool)
    exchange_order_id = f"ex-{uuid4().hex}"
    client_order_id = f"cid-{uuid4().hex}"
    order_id = await _insert_order(
        pool,
        user_id,
        quantity=Decimal("2"),
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
    )
    async with pool.acquire() as conn:
        await conn.execute("UPDATE orders SET status = $1 WHERE order_id = $2", status, order_id)
    return order_id, exchange_order_id, client_order_id


@pytest.mark.parametrize("terminal_status", ["CANCELLED", "REJECTED", "FILLED"])
async def test_late_fill_never_changes_any_terminal_status(pool, terminal_status):
    """DEEPEN-DEPTH negative -- a late fill must not touch an order in any
    terminal status: status/filled_quantity stay and no fill row is created."""
    order_id, ex_id, cid = await _terminal_order(pool, terminal_status)
    trade_id = f"trade-{uuid4().hex}"
    await _deliver(pool, [_order_row(order_id=ex_id, client_id=cid, trade_id=trade_id)])
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
        fills = await conn.fetchval("SELECT count(*) FROM fills WHERE order_id = $1", order_id)
    assert order["status"] == terminal_status
    assert order["filled_quantity"] == Decimal("0")
    assert fills == 0


async def test_late_fill_redelivery_for_terminal_order_stays_single_inbox_row(pool):
    """DEEPEN-DEPTH negative -- replaying the same late fill twice leaves one
    PROCESSED inbox row and still no fill (replay-safe)."""
    order_id, ex_id, cid = await _terminal_order(pool, "CANCELLED")
    trade_id = f"trade-{uuid4().hex}"
    row = _order_row(order_id=ex_id, client_id=cid, trade_id=trade_id)
    await _deliver(pool, [row, row])
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT state FROM provider_event_inbox WHERE provider_event_id = $1",
            f"bitget:orders:fill:{trade_id}",
        )
        fills = await conn.fetchval("SELECT count(*) FROM fills WHERE order_id = $1", order_id)
    assert [r["state"] for r in rows] == ["PROCESSED"]
    assert fills == 0


async def test_terminal_order_late_fill_ingest_failure_propagates_and_keeps_status(pool):
    """DEEPEN-DEPTH failure injection -- an inbox failure while ingesting a
    late fill must surface (not be swallowed) and leave the terminal order
    untouched."""
    order_id, ex_id, cid = await _terminal_order(pool, "CANCELLED")

    class _BoomInbox(InboxProcessor):
        def __init__(self) -> None:
            pass

        async def ingest(self, ev):
            raise RuntimeError("injected terminal-order ingest failure")

    connection = _FakeConnection(
        [_envelope([_order_row(order_id=ex_id, client_id=cid)])], hang=True
    )
    with pytest.raises(RuntimeError, match="injected terminal-order ingest failure"):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            _BoomInbox(),
            connect_fn=lambda url: _TrackingConnectCtx(connection),
            ping_sleep_fn=_never,
        )
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert status == "CANCELLED"


async def test_terminal_order_assertions_go_red_when_order_is_live(pool):
    """DEEPEN-DEPTH gate-red repro -- the same delivery against a live
    (SUBMITTED) order DOES fill it, so the 'status unchanged / zero fills'
    assertions above genuinely discriminate terminal from live orders."""
    order_id, ex_id, cid = await _terminal_order(pool, "SUBMITTED")
    await _deliver(pool, [_order_row(order_id=ex_id, client_id=cid)])
    async with pool.acquire() as conn:
        fills = await conn.fetchval("SELECT count(*) FROM fills WHERE order_id = $1", order_id)
    with pytest.raises(AssertionError):
        assert fills == 0


# Local measurement (best-of-5 cpu): ~2-3 ms per 200 parses; 100 ms budget leaves >=30x headroom.
_TERMINAL_PARSE_BUDGET_MS = 100.0


def _parse_terminal_fill_batch() -> None:
    now = datetime.now(UTC)
    for i in range(200):
        parse_private_order_row(
            _order_row(order_id=f"ex-t-{i}", client_id=f"cid-t-{i}", trade_id=f"t-{i}"),
            received_at=now,
        )


@pytest.mark.perf
def test_terminal_order_fill_parse_within_budget(perf_budget):
    """DEEPEN-DEPTH perf -- best-of-N cpu budget for parsing late-fill rows
    (200 parses; ~2-3 ms locally, 100 ms budget >= 30x headroom)."""
    perf_budget.assert_within(
        _parse_terminal_fill_batch,
        budget_ms=_TERMINAL_PARSE_BUDGET_MS,
        n=5,
        label="terminal_fill_parse",
    )


@pytest.mark.perf
def test_terminal_order_fill_parse_budget_goes_red_under_10x_virtual_delay(perf_budget):
    """DEEPEN-DEPTH gate-red repro -- 10x virtual delay must trip the budget."""
    from tests.support.perf_inject import virtual_delayed_sync

    slow = virtual_delayed_sync(_parse_terminal_fill_batch, _TERMINAL_PARSE_BUDGET_MS * 10 / 1000)
    with pytest.raises(AssertionError, match="terminal_fill_parse"):
        perf_budget.assert_within(
            slow, budget_ms=_TERMINAL_PARSE_BUDGET_MS, n=3, label="terminal_fill_parse"
        )
