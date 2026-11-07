"""L4-20 DEEPEN-DEPTH -- WS fill ingest perf evidence (best-of budget, 10x delay red repro).

Split from test_bitget_private_ws_to_inbox.py to stay under the 800-line threshold.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from websockets.exceptions import ConnectionClosed

from src.exchanges.bitget.private_ws_mixin import subscribe_bitget_orders_to_inbox
from src.services.oms.application.inbox_processor import InboxProcessor
from tests.integration.oms.conftest import create_test_user
from tests.integration.oms.test_bitget_private_ws_to_inbox import (
    _envelope,
    _FakeConnection,
    _insert_order,
    _instant,
    _never,
    _no_rest_calls_adapter,
    _order_row,
    _StopTest,
    _TrackingConnectCtx,
)
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders

_WS_FILL_INGEST_BATCH_BUDGET_MS = 3000.0


async def _ingest_fill_batch(pool, n_events: int = 10) -> int:
    """Seed `n_events` orders, ingest their fills over one fake WS session and
    return the FILLED count. Local measurement (best-of-3 wall): see
    `_WS_FILL_INGEST_BATCH_BUDGET_MS` -- the 3000 ms budget keeps >=30x headroom
    over the measured value."""
    user_id = await create_test_user(pool)
    order_ids: list[UUID] = []
    rows = []
    for _ in range(n_events):
        exchange_order_id = f"ex-{uuid4().hex}"
        client_order_id = f"cid-{uuid4().hex}"
        order_ids.append(
            await _insert_order(
                pool,
                user_id,
                quantity=Decimal("2"),
                exchange_order_id=exchange_order_id,
                client_order_id=client_order_id,
            )
        )
        rows.append(_order_row(order_id=exchange_order_id, client_id=client_order_id))
    connection = _FakeConnection([_envelope(rows)], raise_after=ConnectionClosed(None, None))
    call_count = {"n": 0}

    def connect_fn(url: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _TrackingConnectCtx(connection)
        raise _StopTest

    with pytest.raises(_StopTest):
        await subscribe_bitget_orders_to_inbox(
            _no_rest_calls_adapter(),
            InboxProcessor(pool),
            connect_fn=connect_fn,
            sleep_fn=_instant,
            ping_sleep_fn=_never,
        )
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM orders WHERE order_id = ANY($1::uuid[]) "
                "AND status = 'FILLED'",
                order_ids,
            )
        )


@pytest.mark.perf
async def test_ws_fill_ingest_batch_best_of_within_budget(pool, perf_budget):
    """DEEPEN-DEPTH perf -- best-of-3 wall budget for a 10-fill ingest batch."""
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    best = await perf_budget.best_of_async(lambda: _ingest_fill_batch(pool), n=3, warmup=1)
    assert best.result == 10
    assert best.wall_ms < _WS_FILL_INGEST_BATCH_BUDGET_MS, perf_budget.describe(
        best, budget_ms=_WS_FILL_INGEST_BATCH_BUDGET_MS
    )


@pytest.mark.perf
async def test_ws_fill_ingest_batch_budget_goes_red_under_10x_virtual_delay(pool, perf_budget):
    """DEEPEN-DEPTH gate-red repro -- a virtual delay of 10x the budget must
    push the same wall assertion over the limit."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

    reset_virtual_totals()
    slow = virtual_delayed_async(
        lambda: _ingest_fill_batch(pool, 3), _WS_FILL_INGEST_BATCH_BUDGET_MS * 10 / 1000
    )
    best = await perf_budget.best_of_async(slow, n=2, warmup=0)
    with pytest.raises(AssertionError):
        assert best.wall_ms < _WS_FILL_INGEST_BATCH_BUDGET_MS
    reset_virtual_totals()
