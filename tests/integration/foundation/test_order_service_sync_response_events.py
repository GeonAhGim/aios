"""task-16657 — synchronous non-fill place_order responses carry order_events.

Complements test_order_service_fill_event_atomicity.py (FILLED): REJECTED and
SUBMITTED/ACKNOWLEDGED responses used to update `orders` without any
`order_events` row (sentinel: eventless orders residue).
"""

from __future__ import annotations

import pytest

from src.data.models.trading import OrderStatus
from src.services.order_service import repository as order_repository
from src.services.order_service import submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.test_order_service_fill_event_atomicity import (
    _allow_gate,
    _create_running_execution,
    _market_order,
    pool,  # noqa: F401  (fixture)
)


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (OrderStatus.REJECTED, "VENUE_REJECTED"),
        (OrderStatus.SUBMITTED, "ACK"),
        (OrderStatus.ACKNOWLEDGED, "ACK"),
    ],
)
async def test_sync_non_fill_response_writes_one_event_and_no_fill(pool, status, event):  # noqa: F811
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=status)

    result = await submit_order(
        _market_order(execution_id),
        user_id=user_id,
        adapter=adapter,
        pool=pool,
        pre_submit_gate=_allow_gate,
    )

    async with pool.acquire() as conn:
        events = await conn.fetch(
            "SELECT from_status, to_status, event FROM order_events WHERE order_id = $1",
            result.order_id,
        )
        fills = await conn.fetch("SELECT 1 FROM fills WHERE order_id = $1", result.order_id)
    assert result.status == status
    assert [(e["from_status"], e["to_status"], e["event"]) for e in events] == [
        ("CREATED", status.value, event)
    ]
    assert fills == []


async def test_sync_unmapped_status_fails_closed_and_rolls_back(pool):  # noqa: F811
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.VALIDATED)
    order = _market_order(execution_id)

    with pytest.raises(ValueError, match="no order event mapped"):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT order_id, status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        events = await conn.fetch("SELECT 1 FROM order_events WHERE order_id = $1", row["order_id"])
    assert row["status"] == "CREATED"
    assert events == []


async def test_sync_reject_event_failure_rolls_back_status(pool, monkeypatch):  # noqa: F811
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.REJECTED)

    async def _boom(conn, event):
        raise RuntimeError("injected order_events append failure")

    monkeypatch.setattr(order_repository._order_events, "append", _boom)
    order = _market_order(execution_id)
    with pytest.raises(RuntimeError, match="injected"):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=_allow_gate
        )

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert status == "CREATED"
