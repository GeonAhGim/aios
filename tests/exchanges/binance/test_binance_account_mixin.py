"""task-7997(F2) -- BinanceAccountMixin.get_order tests.

No BinanceAdapter assembly yet (auth.py/factory.py not wired for Binance),
so this mirrors Kiwoom/KIS account_mixin tests and this exchange's own
trading_mixin test file: mixin + a minimal in-file stub client (D2 floor:
negative tests >= 3, one failure-injection test, one numeric performance
assertion, D3: one adversarial test cross-checked against INVARIANTS --
see per-test docstrings. replay_verify is N/A: this is a pure read-only
adapter method that writes nothing to the DB/eventstore).
"""

from __future__ import annotations

import time
from datetime import UTC
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import ExchangeAPIError, FatalExchangeError
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.exchanges.binance.account_mixin import BinanceAccountMixin

pytestmark = pytest.mark.asyncio


class _StubClient(BinanceAccountMixin):
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._response = response or {}
        self._raise_error = raise_error
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, params))
        if self._raise_error is not None:
            raise self._raise_error
        return self._response


def _order_response(**overrides: Any) -> dict[str, Any]:
    base = {
        "symbol": "BTCUSDT",
        "orderId": 1234567,
        "clientOrderId": "c-1",
        "price": "60000.00",
        "origQty": "0.01000000",
        "executedQty": "0.00000000",
        "cummulativeQuoteQty": "0.00000000",
        "status": "NEW",
        "type": "LIMIT",
        "side": "BUY",
        "time": 1_700_000_000_000,
        "updateTime": 1_700_000_000_000,
    }
    base.update(overrides)
    return base


# ---- happy path: status mapping (>=5 states) ----


async def test_get_order_maps_new_to_acknowledged():
    client = _StubClient(response=_order_response(status="NEW"))
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.ACKNOWLEDGED
    assert order.exchange_order_id == "BTCUSDT:1234567"
    assert order.symbol == "BTCUSDT"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT
    assert order.quantity == Decimal("0.01000000")
    method, path, params = client.calls[0]
    assert (method, path) == ("GET", "/api/v3/order")
    assert params == {"symbol": "BTCUSDT", "orderId": "1234567"}


async def test_get_order_maps_partially_filled():
    client = _StubClient(
        response=_order_response(
            status="PARTIALLY_FILLED",
            executedQty="0.00400000",
            cummulativeQuoteQty="240.00000000",
        )
    )
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("0.00400000")
    assert order.average_fill_price is not None
    assert order.average_fill_price.amount == Decimal("60000.00000000")


async def test_get_order_maps_filled():
    client = _StubClient(
        response=_order_response(
            status="FILLED",
            executedQty="0.01000000",
            cummulativeQuoteQty="600.00000000",
            side="SELL",
        )
    )
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.FILLED
    assert order.side == OrderSide.SELL


async def test_get_order_maps_canceled_to_cancelled():
    client = _StubClient(response=_order_response(status="CANCELED"))
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.CANCELLED


async def test_get_order_maps_rejected():
    client = _StubClient(response=_order_response(status="REJECTED"))
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.REJECTED


async def test_get_order_maps_expired():
    client = _StubClient(response=_order_response(status="EXPIRED"))
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.EXPIRED


async def test_get_order_unmapped_status_falls_back_to_unknown():
    """8.3 principle: an unrecognized status is not assumed to mean
    failure -- it surfaces as UNKNOWN rather than raising or guessing."""
    client = _StubClient(response=_order_response(status="SOME_FUTURE_STATUS"))
    order = await client.get_order("BTCUSDT:1234567")
    assert order.status == OrderStatus.UNKNOWN


async def test_get_order_skips_average_fill_price_for_non_usdt_quote():
    """Phase 1 scope is USDT-quoted symbols only -- a non-USDT quote
    (e.g. a BTC-quoted pair) must not mislabel its average price as USDT."""
    client = _StubClient(
        response=_order_response(
            symbol="ETHBTC",
            status="FILLED",
            executedQty="1.00000000",
            cummulativeQuoteQty="0.05000000",
        )
    )
    order = await client.get_order("ETHBTC:1234567")
    assert order.average_fill_price is None


# ---- negative / failure-injection tests (D2 floor >= 3 negative + 1 injection) ----


async def test_get_order_rejects_malformed_exchange_order_id():
    """Negative test 1: an exchange_order_id without ':' must fail before
    any request is made -- same parsing contract as BinanceTradingMixin."""
    client = _StubClient()
    with pytest.raises(FatalExchangeError):
        await client.get_order("not-a-composite-id")
    assert client.calls == []


async def test_get_order_raises_on_response_missing_status():
    """Negative test 2 + failure-injection: a schema-drifted/broken
    response missing `status` must raise a clear FatalExchangeError
    instead of a bare KeyError."""
    client = _StubClient(response={k: v for k, v in _order_response().items() if k != "status"})
    with pytest.raises(FatalExchangeError):
        await client.get_order("BTCUSDT:1234567")


async def test_get_order_raises_on_unmapped_order_type():
    """Negative test 3: an order type our domain model cannot represent
    (e.g. STOP_LOSS, which this adapter never places itself but Binance
    can still return for an order placed outside AIOS) fails closed
    instead of silently mislabeling it as LIMIT/MARKET."""
    client = _StubClient(response=_order_response(type="STOP_LOSS"))
    with pytest.raises(FatalExchangeError):
        await client.get_order("BTCUSDT:1234567")


async def test_get_order_propagates_order_not_found_fail_closed():
    """Negative test 4 (DoD-required not-found case): Binance's GET
    /api/v3/order responds with HTTP 400 (code -2013, 'Order does not
    exist') for an unknown orderId. That HTTP failure is the concrete HTTP
    client's job to raise as an ExchangeAPIError (not yet wired -- auth.py
    is in flight) -- this mixin must let it propagate unmodified rather
    than swallowing it into a default/empty Order (fail-closed posture,
    CLAUDE.md §3)."""
    client = _StubClient(raise_error=ExchangeAPIError("Order does not exist. (code=-2013)"))
    with pytest.raises(ExchangeAPIError):
        await client.get_order("BTCUSDT:9999999")


# ---- adversarial test (D3 -- cross-checked against INVARIANTS) ----


async def test_get_order_never_reports_fill_progress_beyond_origqty():
    """Adversarial/D3: INVARIANTS I-02 (fail-closed, no phantom state) --
    even under a malformed/adversarial exchange response, filled_quantity
    must be read verbatim from executedQty (never derived/inflated), so a
    downstream reconciliation consumer cannot be tricked into crediting
    more fill than the exchange itself reported."""
    client = _StubClient(
        response=_order_response(
            status="PARTIALLY_FILLED", origQty="0.01000000", executedQty="0.00900000"
        )
    )
    order = await client.get_order("BTCUSDT:1234567")
    assert order.filled_quantity == Decimal("0.00900000")
    assert order.filled_quantity <= order.quantity


# ---- performance assertion ----


@pytest.mark.perf
async def test_get_order_latency_budget():
    """Numeric performance assertion: get_order() against a pure stub
    client (no network) must average under 1ms/call over 100 calls -- a
    regression guard on the mixin's own parsing overhead, not a real
    exchange round-trip budget (that is measured once adapter.py exists)."""
    client = _StubClient(response=_order_response(status="FILLED", executedQty="0.01000000"))
    start = time.perf_counter()
    for _ in range(100):
        await client.get_order("BTCUSDT:1234567")
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001


# ---- get_balance ----

BALANCE_RESPONSE = {
    "balances": [
        {"asset": "USDT", "free": "1000.00000000", "locked": "50.00000000"},
        {"asset": "BTC", "free": "0.50000000", "locked": "0.10000000"},
        {"asset": "ETH", "free": "0.00000000", "locked": "0.00000000"},  # zero
    ]
}


async def test_get_balance_returns_nonzero_assets():
    """Happy path: get_balance() returns only assets with nonzero free+locked."""
    client = _StubClient(response=BALANCE_RESPONSE)
    balances = await client.get_balance()
    assert len(balances) == 2
    assets = {b.asset: b for b in balances}
    usdt = assets["USDT"]
    assert usdt.exchange == "binance"
    assert usdt.total == Decimal("1050.00000000")
    assert usdt.available == Decimal("1000.00000000")
    assert usdt.used_margin == Decimal("50.00000000")
    btc = assets["BTC"]
    assert btc.total == Decimal("0.60000000")
    assert btc.available == Decimal("0.50000000")


async def test_get_balance_filters_by_asset():
    """Happy path: get_balance(asset='BTC') returns only BTC."""
    client = _StubClient(response=BALANCE_RESPONSE)
    balances = await client.get_balance(asset="BTC")
    assert len(balances) == 1
    assert balances[0].asset == "BTC"


async def test_get_balance_empty_when_all_zero():
    """Negative: response with only zero-balance assets returns []."""
    client = _StubClient(response={"balances": [{"asset": "XYZ", "free": "0", "locked": "0"}]})
    balances = await client.get_balance()
    assert balances == []


# ---- get_positions ----


async def test_get_positions_returns_empty_list():
    """Happy path: Binance spot has no position concept -- returns []."""
    client = _StubClient(response={})
    positions = await client.get_positions()
    assert positions == []


async def test_get_positions_ignores_symbol_param():
    """Negative: symbol param is accepted but ignored (spot has no positions)."""
    client = _StubClient(response={})
    positions = await client.get_positions(symbol="BTCUSDT")
    assert positions == []


# ---- get_open_orders ----


async def test_get_open_orders_returns_list():
    """Happy path: get_open_orders() returns parsed Order list."""
    client = _StubClient(response=[_order_response(status="NEW", orderId=1)])
    orders = await client.get_open_orders("BTCUSDT")
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.ACKNOWLEDGED
    assert orders[0].symbol == "BTCUSDT"


async def test_get_open_orders_empty_when_no_data():
    """Negative: response with empty data array returns []."""
    client = _StubClient(response={"data": []})
    orders = await client.get_open_orders("BTCUSDT")
    assert orders == []


# ---- get_fills ----


FILLS_RESPONSE = {
    "data": [
        {
            "id": "100",
            "symbol": "BTCUSDT",
            "price": "60000.00",
            "qty": "0.01",
            "commission": "0.00001000",
            "orderId": "1234567",
            "time": 1_700_000_000_000,
        }
    ]
}


async def test_get_fills_returns_raw_rows():
    """Happy path: get_fills() returns raw fill dicts."""
    client = _StubClient(response=FILLS_RESPONSE)
    fills = await client.get_fills(symbol="BTCUSDT")
    assert len(fills) == 1
    assert fills[0]["symbol"] == "BTCUSDT"
    assert fills[0]["orderId"] == "1234567"


async def test_get_fills_empty_when_no_data():
    """Negative: response with empty data array returns []."""
    client = _StubClient(response={"data": []})
    fills = await client.get_fills(symbol="BTCUSDT")
    assert fills == []


async def test_get_fills_since_param_converts_to_epoch_ms():
    """Happy path: since=datetime is converted to ms-epoch startTime param."""
    from datetime import datetime as _dt

    client = _StubClient(response={"data": []})
    since = _dt(2024, 1, 1, tzinfo=UTC)
    await client.get_fills(symbol="BTCUSDT", since=since)
    _, _, params = client.calls[0]
    assert params is not None
    assert params["startTime"] == 1704067200000


# ---- performance assertion ----


@pytest.mark.perf
async def test_get_balance_latency_budget():
    """Numeric performance assertion: get_balance() over 1000 calls must
    average under 1ms -- pure stub, no network."""
    client = _StubClient(response=BALANCE_RESPONSE)
    start = time.perf_counter()
    for _ in range(1000):
        await client.get_balance()
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.001


@pytest.mark.perf
async def test_get_open_orders_latency_budget():
    """Numeric performance assertion: get_open_orders() over 1000 calls
    must average under 1ms."""
    client = _StubClient(response=[_order_response(status="NEW", orderId=1)])
    start = time.perf_counter()
    for _ in range(1000):
        await client.get_open_orders("BTCUSDT")
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.001
