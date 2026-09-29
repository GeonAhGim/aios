"""task-7597(BR-21e) -- OKX WebSocket ticker/orderbook/order-event parsing tests.

D2 floor: negative tests >= 3, one failure-injection test, one numeric
performance assertion. DoD: 3 fake WS message fixtures (normal
trade/ticker, normal orderbook, malformed) parsed; malformed frames raise
instead of being silently dropped (>= 3 negative cases).
"""

from __future__ import annotations

import time

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.okx.websocket import (
    _ack_validator,
    _login_args,
    parse_order_message,
    parse_orderbook_message,
    parse_ticker_message,
)

# ---- normal fixtures ----


def test_parse_ticker_message_normal_frame():
    message = {
        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
        "data": [
            {
                "instId": "BTC-USDT",
                "last": "50000.5",
                "bidPx": "50000.1",
                "askPx": "50000.9",
                "vol24h": "1234.5",
                "ts": "1607417337715",
            }
        ],
    }
    tickers = parse_ticker_message(message)
    assert len(tickers) == 1
    assert tickers[0].symbol == "BTC-USDT"
    assert str(tickers[0].price) == "50000.5"


def test_parse_orderbook_message_normal_frame():
    message = {
        "arg": {"channel": "books", "instId": "BTC-USDT"},
        "data": [
            {
                "instId": "BTC-USDT",
                "asks": [["50001", "0.5", "0", "3"]],
                "bids": [["50000", "1.2", "0", "2"]],
                "ts": "1607417337715",
            }
        ],
    }
    books = parse_orderbook_message(message)
    assert len(books) == 1
    assert books[0].asks[0].price == 50001 or str(books[0].asks[0].price) == "50001"
    assert books[0].bids[0].price == 50000 or str(books[0].bids[0].price) == "50000"


def test_parse_order_message_normal_private_order_event():
    message = {
        "arg": {"channel": "orders", "instType": "SPOT"},
        "data": [
            {
                "instId": "BTC-USDT",
                "ordId": "111",
                "clOrdId": "c-1",
                "sz": "1",
                "side": "buy",
                "ordType": "limit",
                "state": "filled",
                "accFillSz": "1",
                "avgPx": "50000",
                "cTime": "1607417337715",
                "uTime": "1607417337715",
            }
        ],
    }
    orders = parse_order_message(message)
    assert len(orders) == 1
    assert orders[0].exchange_order_id == "BTC-USDT:111"
    assert orders[0].status.value == "FILLED"


# ---- channel filtering (not a malformed case -- just not this parser's channel) ----


def test_parse_ticker_message_ignores_other_channels():
    message = {"arg": {"channel": "books"}, "data": [{}]}
    assert parse_ticker_message(message) == []


# ---- malformed frames -- must raise, never silently drop (D2 floor: negative >= 3) ----


def test_parse_ticker_message_raises_on_missing_last_field():
    """Malformed 1: a tickers row missing the required `last` field."""
    message = {
        "arg": {"channel": "tickers"},
        "data": [{"instId": "BTC-USDT", "ts": "1607417337715"}],
    }
    with pytest.raises(FatalExchangeError):
        parse_ticker_message(message)


def test_parse_orderbook_message_raises_on_missing_bids_field():
    """Malformed 2: a books row missing the required `bids` field."""
    message = {
        "arg": {"channel": "books"},
        "data": [{"instId": "BTC-USDT", "asks": [["1", "1", "0", "1"]], "ts": "1"}],
    }
    with pytest.raises(FatalExchangeError):
        parse_orderbook_message(message)


def test_parse_order_message_raises_on_missing_sz_field():
    """Malformed 3: an orders row missing the required `sz` field."""
    message = {
        "arg": {"channel": "orders"},
        "data": [{"instId": "BTC-USDT", "ordId": "111", "side": "buy"}],
    }
    with pytest.raises(FatalExchangeError):
        parse_order_message(message)


def test_parse_ticker_message_raises_on_non_numeric_price():
    """Malformed 4 / failure-injection: a non-numeric `last` value (venue
    sends a corrupted string instead of a price) must not crash with an
    unrelated Decimal exception type leaking to the caller uncaught --
    still raises FatalExchangeError, just via a different underlying cause."""
    message = {
        "arg": {"channel": "tickers"},
        "data": [
            {
                "instId": "BTC-USDT",
                "last": "not-a-number",
                "ts": "1607417337715",
            }
        ],
    }
    with pytest.raises(FatalExchangeError):
        parse_ticker_message(message)


# ---- login/ack helpers ----


def test_login_args_builds_signed_login_payload():
    class _Client:
        _api_key = "key"
        _api_secret = "secret"
        _api_passphrase = "phrase"

    args = _login_args(_Client(), now=lambda: 1607417337.0)
    assert args == [
        {
            "apiKey": "key",
            "passphrase": "phrase",
            "timestamp": "1607417337",
            "sign": args[0]["sign"],  # signature covered by test_okx_auth.py's fixed-input tests
        }
    ]
    assert len(args[0]["sign"]) > 0


def test_ack_validator_flags_error_event_as_failed_ack():
    ack = _ack_validator({"event": "error", "code": "60012", "msg": "bad request"})
    assert ack.is_ack is True
    assert ack.ok is False
    assert ack.detail == "bad request"


def test_ack_validator_treats_data_frame_as_not_ack():
    ack = _ack_validator({"arg": {"channel": "tickers"}, "data": []})
    assert ack.is_ack is False


# ---- numeric performance assertion ----


@pytest.mark.perf
def test_parse_ticker_message_latency_budget():
    """Numeric performance assertion: pure parsing (no network/websocket)
    must average under 1ms across 200 calls."""
    message = {
        "arg": {"channel": "tickers"},
        "data": [
            {
                "instId": "BTC-USDT",
                "last": "50000.5",
                "bidPx": "50000.1",
                "askPx": "50000.9",
                "vol24h": "1234.5",
                "ts": "1607417337715",
            }
        ],
    }
    start = time.perf_counter()
    for _ in range(200):
        parse_ticker_message(message)
    elapsed = time.perf_counter() - start
    assert elapsed / 200 < 0.001
