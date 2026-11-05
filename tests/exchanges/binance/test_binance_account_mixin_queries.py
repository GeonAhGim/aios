"""BR-22c -- BinanceAccountMixin get_balance/get_positions/get_open_orders/get_fills.

Fake HTTP only (no real keys). Split from test_binance_account_mixin.py
(get_order) to keep each file under the 300-line cap. replay_verify is
N/A: read-only adapter methods that write nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderStatus
from src.exchanges.binance.account_mixin import BinanceAccountMixin
from src.exchanges.common.adapter import UnsupportedCapabilityError

pytestmark = pytest.mark.asyncio


class _StubClient(BinanceAccountMixin):
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _unsupported(self, capability: str) -> Exception:
        return UnsupportedCapabilityError(capability, type(self).__name__)

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self.calls.append((method, path, params))
        if self._error is not None:
            raise self._error
        return self._response


def _account() -> dict[str, Any]:
    return {
        "updateTime": 1_700_000_000_000,
        "balances": [
            {"asset": "BTC", "free": "0.50000000", "locked": "0.10000000"},
            {"asset": "USDT", "free": "100.5", "locked": "0"},
        ],
    }


def _open_order(**kw: Any) -> dict[str, Any]:
    base = {
        "symbol": "BTCUSDT",
        "orderId": 9,
        "clientOrderId": "c-9",
        "price": "60000",
        "origQty": "0.01",
        "executedQty": "0",
        "cummulativeQuoteQty": "0",
        "status": "NEW",
        "type": "LIMIT",
        "side": "BUY",
        "time": 1_700_000_000_000,
        "updateTime": 1_700_000_000_000,
    }
    base.update(kw)
    return base


# ---- get_balance ----


async def test_get_balance_happy_path_decimal_and_total():
    client = _StubClient(_account())
    result = await client.get_balance()
    assert client.calls[0][:2] == ("GET", "/api/v3/account")
    btc = result[0]
    assert (btc.asset, btc.total, btc.available) == ("BTC", Decimal("0.6"), Decimal("0.5"))
    assert btc.exchange == "binance" and btc.updated_at.tzinfo is not None


async def test_get_balance_filters_by_asset():
    result = await _StubClient(_account()).get_balance("USDT")
    assert [b.asset for b in result] == ["USDT"]


async def test_get_balance_malformed_response_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _StubClient({"updateTime": 1}).get_balance()


async def test_get_balance_bad_number_fails_closed():
    bad = _account()
    bad["balances"][0]["free"] = "abc"
    with pytest.raises(FatalExchangeError):
        await _StubClient(bad).get_balance()


async def test_get_balance_propagates_transport_error():
    with pytest.raises(ConnectionError):
        await _StubClient(error=ConnectionError("down")).get_balance()


# ---- get_positions (unsupported on spot) ----


async def test_get_positions_raises_unsupported_capability():
    with pytest.raises(UnsupportedCapabilityError):
        await _StubClient([]).get_positions()


# ---- get_open_orders ----


async def test_get_open_orders_happy_path_maps_orders():
    client = _StubClient([_open_order(), _open_order(orderId=10, status="PARTIALLY_FILLED")])
    orders = await client.get_open_orders("BTCUSDT")
    assert client.calls[0] == ("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"})
    assert [o.exchange_order_id for o in orders] == ["BTCUSDT:9", "BTCUSDT:10"]
    assert orders[1].status is OrderStatus.PARTIALLY_FILLED


async def test_get_open_orders_empty_list_is_a_fact_without_symbol_param():
    client = _StubClient([])
    assert await client.get_open_orders() == []
    assert client.calls[0][2] == {}


async def test_get_open_orders_non_list_response_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _StubClient({"code": -1}).get_open_orders()


async def test_get_open_orders_row_missing_field_fails_closed():
    row = _open_order()
    del row["status"]
    with pytest.raises(FatalExchangeError):
        await _StubClient([row]).get_open_orders()


# ---- get_fills ----


async def test_get_fills_happy_path_passes_filters():
    rows = [{"id": 1, "orderId": 9, "price": "60000", "qty": "0.01"}]
    client = _StubClient(rows)
    since = datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    result = await client.get_fills("BTCUSDT", order_id="BTCUSDT:9", since=since)
    assert result == rows
    assert client.calls[0] == (
        "GET",
        "/api/v3/myTrades",
        {"symbol": "BTCUSDT", "orderId": "9", "startTime": 1_700_000_000_000},
    )


async def test_get_fills_requires_symbol():
    with pytest.raises(FatalExchangeError):
        await _StubClient([]).get_fills()


async def test_get_fills_rejects_naive_since():
    with pytest.raises(FatalExchangeError):
        await _StubClient([]).get_fills("BTCUSDT", since=datetime(2023, 1, 1))


async def test_get_fills_non_list_response_fails_closed():
    with pytest.raises(FatalExchangeError):
        await _StubClient({"code": -1}).get_fills("BTCUSDT")
