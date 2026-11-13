"""task-24799 DEEPEN-DEPTH BR-16b — Bitget order path D1 -> D2 supplement.

Sibling of test_bitget_orders.py (kept untouched): negative x3, failure injection,
a perf budget and its 10x virtual-delay gate-red reproduction.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError
from src.data.models.trading import OrderStatus
from src.services.oms.domain.errors import OrderValidationError
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_OK_PLACE = {"code": "00000", "msg": "success", "requestTime": 1, "data": {"orderId": "999"}}


async def _no_sleep(seconds: float) -> None:
    return None


async def test_place_order_rejects_lot_misaligned_quantity_without_calling_exchange(
    make_adapter, new_order
):
    """negative 1 — a sub-lot quantity is rejected before any HTTP call."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("exchange must not be called on validation failure")

    adapter = make_adapter(handler)
    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(new_order(quantity="0.0000000001"))
    assert exc_info.value.reason == "LOT"


async def test_cancel_order_signature_error_raises_fatal(make_adapter, json_response):
    """negative 2 — a fatal exchange code on cancel is never reported as success."""
    adapter = make_adapter(
        lambda request: json_response({"code": "40012", "msg": "invalid sign", "data": {}})
    )
    with pytest.raises(FatalExchangeError):
        await adapter.cancel_order("999")


async def test_place_batch_orders_failure_list_marks_rejected_not_submitted(
    make_adapter, json_response, new_order
):
    """negative 3 — an order listed only in failureList ends REJECTED with no exchange id."""
    adapter = make_adapter(
        lambda request: json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"successList": [], "failureList": [{"clientOid": "c-1"}]},
            }
        )
    )
    (result,) = await adapter.place_batch_orders([new_order()])
    assert result.status == OrderStatus.REJECTED
    assert result.exchange_order_id is None


async def test_cancel_order_retryable_error_with_live_order_reraises(
    make_adapter, json_response, order_row
):
    """failure injection — a lost cancel response where the order is still live must not
    be reported as cancelled; the original retryable error propagates."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return json_response({"code": "99999", "msg": "internal error", "data": {}})
        return json_response(
            {"code": "00000", "msg": "success", "requestTime": 1, "data": order_row(status="live")}
        )

    adapter = make_adapter(handler, sleep_fn=_no_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.cancel_order("999")


# Perf budget (task-24799): place_order over MockTransport measured ~2.5ms wall locally
# (best-of-5, warmup 1); the budget keeps a >30x margin.
_PLACE_BUDGET_MS = 100.0


async def _measure_place(
    perf_budget: PerfBudget, make_adapter, json_response, new_order, *, delay_s=0.0
):
    adapter = make_adapter(lambda request: json_response(_OK_PLACE))
    call = virtual_delayed_async(adapter.place_order, delay_s) if delay_s else adapter.place_order

    async def _run():
        return await call(new_order())

    return await perf_budget.best_of_async(_run, n=5, warmup=1)


@pytest.mark.perf
async def test_place_order_perf_budget(perf_budget, make_adapter, json_response, new_order):
    """perf assertion — place_order wall latency stays under the budget."""
    reset_virtual_totals()
    sample = await _measure_place(perf_budget, make_adapter, json_response, new_order)
    assert sample.wall_ms < _PLACE_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PLACE_BUDGET_MS
    )


@pytest.mark.perf
async def test_place_order_perf_gate_red_with_10x_injection(
    perf_budget, make_adapter, json_response, new_order
):
    """gate-red reproduction — a 10x virtual delay turns the same assertion red."""
    reset_virtual_totals()
    sample = await _measure_place(
        perf_budget, make_adapter, json_response, new_order, delay_s=_PLACE_BUDGET_MS * 10 / 1000
    )
    assert not (sample.wall_ms < _PLACE_BUDGET_MS)
    reset_virtual_totals()
