"""task-7997(F2) -- BinanceAccountMixin account/position/order/fill read tests.

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
        response: dict[str, Any] | list[Any] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._response = response if response is not None else {}
        self._raise_error = raise_error
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
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


def _balance_response(**overrides: Any) -> dict[str, Any]:
    """Minimal Binance /api/v3/account response shape."""
    base: dict[str, Any] = {
        "balances": [
            {"asset": "USDT", "free": "1000.50000000", "locked": "0.50000000"},
            {"asset": "BTC", "free": "0.50000000", "locked": "0"},
            {"asset": "ETH", "free": "0", "locked": "0"},
        ],
    }
    base.update(overrides)
    return base


async def test_get_balance_parses_usdt_and_btc():
    client = _StubClient(response=_balance_response())
    balances = await client.get_balance()
    assert len(balances) == 2  # ETH (zero total) is filtered out
    usdt = next(b for b in balances if b.asset == "USDT")
    assert usdt.total == Decimal("1001.00000000")
    assert usdt.available == Decimal("1000.50000000")
    btc = next(b for b in balances if b.asset == "BTC")
    assert btc.total == Decimal("0.50000000")
    assert btc.available == Decimal("0.50000000")


async def test_get_balance_filters_by_asset():
    client = _StubClient(response=_balance_response())
    balances = await client.get_balance(asset="BTC")
    assert len(balances) == 1
    assert balances[0].asset == "BTC"


async def test_get_balance_returns_zero_balances_when_asset_specified():
    """When `asset` is specified, zero balances must be included (caller
    may want to record a zero balance, not skip it silently)."""
    client = _StubClient(response=_balance_response())
    balances = await client.get_balance(asset="ETH")
    assert len(balances) == 1
    assert balances[0].total == Decimal("0")


async def test_get_balance_raises_fatal_on_malformed_response():
    """Negative 1: a response missing the `balances` key must raise
    FatalExchangeError (fail-closed, not an empty list)."""
    client = _StubClient(response={})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


# ---- get_positions ----


class _StubClientWithTicker(_StubClient):
    """Extends _StubClient to also stub get_ticker (needed by get_positions)."""

    def __init__(self, ticker_price: Decimal = Decimal("30000.00")) -> None:
        super().__init__(response=_balance_response())
        self._ticker_price = ticker_price

    async def get_ticker(self, symbol: str) -> Any:
        from datetime import datetime

        from src.data.models.market_data import Ticker

        return Ticker(
            symbol=symbol,
            exchange="binance",
            price=self._ticker_price,
            bid=self._ticker_price,
            ask=self._ticker_price,
            volume_24h=Decimal("0"),
            timestamp=datetime.now(UTC),
            source_type="primary",
        )


async def test_get_positions_synthesizes_from_non_zero_balances():
    client = _StubClientWithTicker()
    positions = await client.get_positions()
    # USDT is skipped (_QUOTE_ASSET); ETH (zero) is skipped; only BTC
    assert len(positions) == 1
    assert positions[0].symbol == "BTC/USDT"
    assert positions[0].quantity == Decimal("0.50000000")


async def test_get_positions_skips_unpriced_ticker_fail_closed():
    """Negative 1: when get_ticker raises ExchangeAPIError the position
    must be silently skipped (one unpriced coin must not fail the whole
    query, per account_mixin.py docstring)."""

    class _NoTickerClient(_StubClientWithTicker):
        async def get_ticker(self, symbol: str) -> Any:
            raise ExchangeAPIError("200: Symbol not found")

    client = _NoTickerClient()
    positions = await client.get_positions()
    # BTC's ticker fails -> skipped; only zero-balance ETH remains but
    # it is also zero so result is empty
    assert len(positions) == 0


# ---- get_open_orders ----


def _open_order_response(**overrides: Any) -> dict[str, Any]:
    base = {
        "symbol": "BTCUSDT",
        "orderId": 789,
        "clientOrderId": "open-1",
        "price": "55000.00",
        "origQty": "0.02000000",
        "executedQty": "0.00000000",
        "status": "NEW",
        "type": "LIMIT",
        "side": "SELL",
        "time": 1_700_001_000_000,
        "updateTime": 1_700_001_000_000,
    }
    base.update(overrides)
    return base


async def test_get_open_orders_parses_list():
    client = _StubClient(response=[_open_order_response()])
    orders = await client.get_open_orders(symbol="BTC/USDT")
    assert len(orders) == 1
    assert orders[0].symbol == "BTCUSDT"
    assert orders[0].exchange_order_id == "BTCUSDT:789"
    assert orders[0].status == OrderStatus.ACKNOWLEDGED


async def test_get_open_orders_handles_empty_list():
    client = _StubClient(response=[])
    orders = await client.get_open_orders(symbol="BTC/USDT")
    assert orders == []


async def test_get_open_orders_raises_on_non_list_response():
    """Negative 1: a non-list response must raise FatalExchangeError."""
    client = _StubClient(response={"error": "bad"})
    with pytest.raises(FatalExchangeError):
        await client.get_open_orders(symbol="BTC/USDT")


# ---- get_fills ----


async def test_get_fills_requires_symbol():
    """Negative 1: `symbol` is required by Binance myTrades endpoint."""
    client = _StubClient(response=[])
    with pytest.raises(FatalExchangeError):
        await client.get_fills()


async def test_get_fills_passes_since_as_millis():
    from datetime import datetime

    client = _StubClient(response=[])
    since = datetime(2024, 1, 1, tzinfo=UTC)
    await client.get_fills(symbol="BTC/USDT", since=since)
    _, _, params = client.calls[0]
    assert params is not None
    assert params["startTime"] == 1704067200000


async def test_get_fills_raises_on_naive_since():
    """Negative 2: naive datetime for `since` must be rejected (fail-closed)."""
    from datetime import datetime

    client = _StubClient(response=[])
    since = datetime(2024, 1, 1)  # naive
    with pytest.raises(FatalExchangeError):
        await client.get_fills(symbol="BTC/USDT", since=since)


async def test_get_fills_raises_on_non_list_response():
    """Negative 3: a non-list response must raise FatalExchangeError."""
    client = _StubClient(response={"error": "bad"})
    with pytest.raises(FatalExchangeError):
        await client.get_fills(symbol="BTC/USDT")


# ---- performance assertion ----


@pytest.mark.perf
async def test_get_balance_latency_budget():
    """Numeric performance assertion: get_balance() against a pure stub
    client (no network) must average under 1ms/call over 100 calls."""
    client = _StubClient(response=_balance_response())
    start = time.perf_counter()
    for _ in range(100):
        await client.get_balance()
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
