"""task-22260 (BR-22c) -- BinanceAccountMixin get_balance/get_positions/
get_open_orders/get_fills tests against fake HTTP payloads (no real key).
Payload shapes follow the public Spot API docs (live responses UNVERIFIED)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.exceptions import ExchangeAPIError, FatalExchangeError
from src.data.models.trading import OrderStatus
from src.exchanges.binance.account_mixin import BinanceAccountMixin
from src.exchanges.binance.factory import BinanceAdapter

pytestmark = pytest.mark.asyncio


class _Stub(BinanceAccountMixin):
    def __init__(self, response: Any = None, *, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, params))
        if self._error is not None:
            raise self._error
        return self._response

    async def get_ticker(self, symbol: str) -> Any:
        if symbol.startswith("BAD"):
            raise ExchangeAPIError("no ticker")
        return SimpleNamespace(price=Decimal("50000"))


def _account() -> dict[str, Any]:
    return {
        "balances": [
            {"asset": "BTC", "free": "0.5", "locked": "0.25"},
            {"asset": "USDT", "free": "100", "locked": "0"},
            {"asset": "ETH", "free": "0", "locked": "0"},
        ]
    }


def _open_order(**kw: Any) -> dict[str, Any]:
    base = {
        "symbol": "BTCUSDT",
        "orderId": 7,
        "clientOrderId": "c1",
        "price": "1",
        "origQty": "2",
        "executedQty": "0",
        "cummulativeQuoteQty": "0",
        "status": "NEW",
        "type": "LIMIT",
        "side": "BUY",
        "time": 1700000000000,
        "updateTime": 1700000001000,
    }
    return base | kw


async def test_get_balance_happy_skips_zero_rows():
    stub = _Stub(_account())
    bals = await stub.get_balance()
    assert [(b.asset, b.total, b.available) for b in bals] == [
        ("BTC", Decimal("0.75"), Decimal("0.5")),
        ("USDT", Decimal("100"), Decimal("100")),
    ]
    assert stub.calls[0][:2] == ("GET", "/api/v3/account")


async def test_get_balance_asset_filter():
    bals = await _Stub(_account()).get_balance("ETH")
    assert [b.asset for b in bals] == ["ETH"]


async def test_get_balance_malformed_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _Stub({"balances": [{"asset": "BTC"}]}).get_balance()


async def test_get_balance_propagates_transport_error():
    with pytest.raises(FatalExchangeError):
        await _Stub(error=FatalExchangeError("boom")).get_balance()


async def test_get_positions_happy_excludes_quote():
    pos = await _Stub(_account()).get_positions()
    assert [(p.symbol, p.quantity) for p in pos] == [("BTC/USDT", Decimal("0.75"))]
    assert pos[0].current_price.amount == Decimal("50000")


async def test_get_positions_skips_unpriced_coin():
    stub = _Stub({"balances": [{"asset": "BAD", "free": "1", "locked": "0"}]})
    assert await stub.get_positions() == []


async def test_get_open_orders_happy():
    stub = _Stub([_open_order(), _open_order(orderId=8)])
    orders = await stub.get_open_orders("BTC/USDT")
    assert [o.exchange_order_id for o in orders] == ["BTCUSDT:7", "BTCUSDT:8"]
    assert orders[0].status is OrderStatus.ACKNOWLEDGED
    assert stub.calls[0] == ("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"})


async def test_get_open_orders_non_list_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _Stub({"code": -1}).get_open_orders()


async def test_get_open_orders_row_missing_field_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _Stub([{"symbol": "BTCUSDT", "orderId": 1}]).get_open_orders()


async def test_get_open_orders_rejects_raw_symbol():
    with pytest.raises(Exception):  # noqa: B017 -- canonical "/" form required
        await _Stub([]).get_open_orders("BTCUSDT")


async def test_get_fills_happy_passes_filters():
    rows = [{"id": 1, "orderId": 9, "price": "1", "qty": "2"}]
    stub = _Stub(rows)
    since = datetime(2026, 1, 1, tzinfo=UTC)
    assert await stub.get_fills("BTC/USDT", order_id="9", since=since) == rows
    assert stub.calls[0][2] == {
        "symbol": "BTCUSDT",
        "orderId": "9",
        "startTime": int(since.timestamp() * 1000),
    }


async def test_get_fills_requires_symbol():
    with pytest.raises(FatalExchangeError):
        await _Stub([]).get_fills()


async def test_get_fills_rejects_naive_since():
    with pytest.raises(FatalExchangeError):
        await _Stub([]).get_fills("BTC/USDT", since=datetime(2026, 1, 1))


async def test_get_fills_non_list_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _Stub({"msg": "x"}).get_fills("BTC/USDT")


async def test_unsupported_extension_raises_on_adapter():
    with pytest.raises(Exception, match="find_order_by_client_id"):
        await BinanceAdapter("k", "s").find_order_by_client_id("c")
