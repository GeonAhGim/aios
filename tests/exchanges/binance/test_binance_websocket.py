"""BR-22e -- Binance WS parser tests with fake frames (no real socket)."""

from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.exchanges.binance.websocket import (
    parse_order_ws_message,
    parse_orderbook_ws_message,
    parse_ticker_ws_message,
)

_EXEC = {
    "e": "executionReport",
    "E": 1700000000000,
    "s": "BTCUSDT",
    "c": "cid-1",
    "S": "BUY",
    "o": "LIMIT",
    "q": "2",
    "p": "100",
    "X": "PARTIALLY_FILLED",
    "i": 42,
    "z": "1",
    "Z": "100",
}
_TICKER = {
    "e": "24hrTicker",
    "E": 1700000000000,
    "s": "BTCUSDT",
    "c": "100.5",
    "b": "100.4",
    "a": "100.6",
    "v": "12.5",
}
_DEPTH = {"lastUpdateId": 1, "bids": [["100", "1"]], "asks": [["101", "2"]]}


def test_execution_report_parsed() -> None:
    order = parse_order_ws_message(_EXEC)
    assert order.symbol == "BTC/USDT"
    assert order.exchange_order_id == "BTCUSDT:42"
    assert order.side == OrderSide.BUY and order.order_type == OrderType.LIMIT
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("1")
    assert order.average_fill_price is not None
    assert order.average_fill_price.amount == Decimal("100")


def test_ticker_parsed_and_combined_stream_unwrapped() -> None:
    ticker = parse_ticker_ws_message({"stream": "btcusdt@ticker", "data": _TICKER})
    assert ticker.symbol == "BTC/USDT"
    assert ticker.price == Decimal("100.5") and ticker.bid == Decimal("100.4")


def test_orderbook_parsed() -> None:
    book = parse_orderbook_ws_message(_DEPTH, symbol="BTC/USDT")
    assert book.bids[0].price == Decimal("100")
    assert book.asks[0].quantity == Decimal("2")


def test_unknown_status_maps_to_unknown() -> None:
    assert parse_order_ws_message({**_EXEC, "X": "WEIRD"}).status == OrderStatus.UNKNOWN


@pytest.mark.parametrize("bad", ["not-json-object", [1, 2], {"e": "24hrTicker"}, {"c": "1"}])
def test_malformed_ticker_raises(bad: object) -> None:
    with pytest.raises(FatalExchangeError):
        parse_ticker_ws_message(bad)


@pytest.mark.parametrize(
    "bad",
    [{}, {"bids": [["x", "1"]], "asks": []}, {"bids": [["1"]], "asks": []}, None],
)
def test_malformed_orderbook_raises(bad: object) -> None:
    with pytest.raises(FatalExchangeError):
        parse_orderbook_ws_message(bad, symbol="BTC/USDT")


@pytest.mark.parametrize(
    "bad",
    [
        {"e": "outboundAccountPosition"},
        {**_EXEC, "q": "abc"},
        {**_EXEC, "S": "HOLD"},
        {k: v for k, v in _EXEC.items() if k != "c"},
        {"e": "listenKeyExpired", "E": 1},
    ],
)
def test_malformed_order_raises(bad: dict[str, Any]) -> None:
    with pytest.raises(FatalExchangeError):
        parse_order_ws_message(bad)
