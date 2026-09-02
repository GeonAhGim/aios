"""02b_bitget_api_v2_full_spec_v1.md §6 — WebSocket 메시지 파싱 단위테스트.

실제 소켓 없이도 검증 가능한 부분(순수 파싱 함수)만 다룬다 — 연결관리/
재연결 루프는 tests/integration/test_bitget_websocket.py 참조.
"""
from decimal import Decimal

from src.data.models.trading import OrderStatus
from src.exchanges.bitget.market_data_mixin import (
    _build_login_message,
    parse_candle_ws_message,
    parse_order_ws_message,
    parse_orderbook_ws_message,
    parse_ticker_ws_message,
)


def test_parse_ticker_ws_message_ignores_subscribe_ack():
    assert parse_ticker_ws_message({"event": "subscribe", "arg": {}}) == []


def test_parse_ticker_ws_message_ignores_error_event():
    assert parse_ticker_ws_message({"event": "error", "code": "30001"}) == []


def test_parse_ticker_ws_message_parses_data_rows():
    message = {
        "action": "snapshot",
        "arg": {"instType": "SPOT", "channel": "ticker", "instId": "BTCUSDT"},
        "data": [
            {
                "symbol": "BTCUSDT",
                "lastPr": "80000",
                "bidPr": "79999",
                "askPr": "80001",
                "baseVolume": "100",
                "ts": "1700000000000",
            }
        ],
    }

    tickers = parse_ticker_ws_message(message)

    assert len(tickers) == 1
    assert tickers[0].price == Decimal("80000")
    assert tickers[0].symbol == "BTC/USDT"


def test_parse_candle_ws_message_ignores_control_message():
    assert parse_candle_ws_message({"event": "subscribe"}, symbol="BTC/USDT", timeframe="1m") == []


def test_parse_candle_ws_message_ignores_empty_data():
    assert parse_candle_ws_message({"data": []}, symbol="BTC/USDT", timeframe="1m") == []


def test_parse_candle_ws_message_parses_rows():
    message = {
        "action": "update",
        "data": [["1700000000000", "80000", "81000", "79500", "80500", "12.3"]],
    }

    candles = parse_candle_ws_message(message, symbol="BTC/USDT", timeframe="1m")

    assert len(candles) == 1
    assert candles[0].close == Decimal("80500")
    assert candles[0].timeframe == "1m"


def test_parse_orderbook_ws_message_ignores_control_message():
    assert parse_orderbook_ws_message({"event": "error"}, symbol="BTC/USDT") is None


def test_parse_orderbook_ws_message_ignores_empty_data():
    assert parse_orderbook_ws_message({"data": []}, symbol="BTC/USDT") is None


def test_parse_orderbook_ws_message_parses_snapshot():
    message = {
        "action": "snapshot",
        "data": [{"bids": [["80000", "1.5"]], "asks": [["80100", "2.0"]]}],
    }

    book = parse_orderbook_ws_message(message, symbol="BTC/USDT")

    assert book is not None
    assert book.bids[0].price == Decimal("80000")
    assert book.asks[0].quantity == Decimal("2.0")
    assert book.symbol == "BTC/USDT"


def test_build_login_message_has_expected_shape():
    login_msg = _build_login_message("key123", "secret456", "phrase789")

    assert login_msg["op"] == "login"
    arg = login_msg["args"][0]
    assert arg["apiKey"] == "key123"
    assert arg["passphrase"] == "phrase789"
    assert arg["timestamp"].isdigit()
    assert isinstance(arg["sign"], str) and len(arg["sign"]) > 0


def test_parse_order_ws_message_ignores_login_ack():
    assert parse_order_ws_message({"event": "login", "code": "0"}) == []


def test_parse_order_ws_message_ignores_error_event():
    assert parse_order_ws_message({"event": "error", "code": "30005"}) == []


def test_parse_order_ws_message_parses_data_rows():
    message = {
        "action": "snapshot",
        "data": [
            {
                "orderId": "12345",
                "clientOid": "abc",
                "symbol": "BTCUSDT",
                "side": "buy",
                "orderType": "limit",
                "size": "0.5",
                "status": "live",
            }
        ],
    }

    orders = parse_order_ws_message(message)

    assert len(orders) == 1
    assert orders[0].exchange_order_id == "12345"
    assert orders[0].status == OrderStatus.ACKNOWLEDGED
