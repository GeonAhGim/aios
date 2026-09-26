"""task-8073 — FD-4.1 tick/lot/min_notional pre-validation for place_order.

Split out of test_bitget_orders.py (task-4225 precedent: keep each file under
the 500-line warn threshold instead of growing an already-large file).
"""

import json
from decimal import Decimal

import httpx
import pytest

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderStatus, OrderType
from src.services.oms.domain.errors import OrderValidationError


def _fail_if_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError("place_order must not call the exchange on validation failure")


async def test_place_order_rejects_tick_misaligned_price_without_calling_exchange(
    make_adapter, new_order
):
    """negative: BTC/USDT tick=0.01 — a price with a sub-tick fraction must be
    rejected fail-closed, before any HTTP call reaches the exchange."""
    adapter = make_adapter(_fail_if_called)
    order = new_order(
        order_type=OrderType.LIMIT,
        price=Money(amount=Decimal("80000.005"), currency=Currency.USDT),
        quantity=Decimal("0.01"),
    )

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "TICK"


async def test_place_order_rejects_min_notional_shortfall_without_calling_exchange(
    make_adapter, new_order
):
    """negative: BTC/USDT min_notional=1 USDT — price*qty below that must be
    rejected fail-closed, before any HTTP call reaches the exchange."""
    adapter = make_adapter(_fail_if_called)
    order = new_order(
        order_type=OrderType.LIMIT,
        price=Money(amount=Decimal("0.50"), currency=Currency.USDT),
        quantity=Decimal("0.000001"),
    )

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "MIN_NOTIONAL"


async def test_place_order_rejects_lot_misaligned_quantity_without_calling_exchange(
    make_adapter, new_order
):
    """negative: BTC/USDT qty_lot=0.000001 (market order, so tick/min_notional
    are out of scope) — a quantity that is not an integer multiple of the lot
    unit must be rejected fail-closed, before any HTTP call reaches the
    exchange."""
    adapter = make_adapter(_fail_if_called)
    order = new_order(
        order_type=OrderType.MARKET,
        price=None,
        quantity=Decimal("0.0000015"),
    )

    with pytest.raises(OrderValidationError) as exc_info:
        await adapter.place_order(order)

    assert exc_info.value.reason == "LOT"


async def test_place_order_precheck_failure_does_not_return_a_submitted_order(
    make_adapter, new_order
):
    """failure-injection: a pre-check rejection must propagate as
    OrderValidationError, not be swallowed into a fake SUBMITTED result
    (KIS test_trading_mixin_precheck.py precedent for the same audit item)."""
    adapter = make_adapter(_fail_if_called)
    order = new_order(
        order_type=OrderType.LIMIT,
        price=Money(amount=Decimal("80000.005"), currency=Currency.USDT),
        quantity=Decimal("0.01"),
    )

    try:
        result = await adapter.place_order(order)
    except OrderValidationError:
        result = None

    assert result is None


async def test_place_order_accepts_tick_and_notional_aligned_limit_order(
    make_adapter, json_response, new_order
):
    """regression: a properly tick/lot-aligned limit order with sufficient
    notional must still reach the exchange and succeed (no false-positive
    rejection)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["price"] == "80000.00"
        assert body["size"] == "0.01"
        return json_response(
            {
                "code": "00000",
                "msg": "success",
                "requestTime": 1,
                "data": {"orderId": "999", "clientOid": "c-1"},
            }
        )

    adapter = make_adapter(handler)
    order = new_order(
        order_type=OrderType.LIMIT,
        price=Money(amount=Decimal("80000.00"), currency=Currency.USDT),
        quantity=Decimal("0.01"),
    )

    result = await adapter.place_order(order)

    assert result.exchange_order_id == "999"
    assert result.status == OrderStatus.SUBMITTED
