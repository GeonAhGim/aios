"""BR-22e(task-21977) — Binance WebSocket 메시지 파싱 단위 테스트.

Spec: docs/exchanges/ADDING_AN_EXCHANGE.md 1단계 계속, Binance.
subscribe_ticker_stream/subscribe_order_stream — Binance public WS
메시지 파싱을 fake fixture로 단위 테스트(실 WS 연결 불필요).
파싱 실패는 명시적 예외.

DoD:
1) fake WS 메시지 3종(정상 체결/정상 호가/malformed) 파싱 테스트
2) malformed 메시지는 예외 발생 — negative 최소 3건
3) 파일당 300줄 상한

D2 evidence: negative ≥3, failure-injection 1, perf 1, gate-red repro 1.
replay_verify: N/A — 순수 파서 함수만 테스트.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.market_data import OrderBook, Ticker
from src.exchanges.binance.websocket_parsing import (
    classify_binance_ws_event,
    parse_binance_24hr_ticker_message,
    parse_binance_orderbook_message,
    parse_binance_trade_message,
    parse_listen_key_response,
)

# ============================================================================
# Fake fixtures — Binance WS 실제 응답 형식 기반
# ============================================================================


def _trade_fixture(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "e": "trade",
        "E": 1700000000000,
        "s": "BTCUSDT",
        "t": 987654321,
        "p": "60000.00",
        "q": "0.01000000",
        "T": 1700000000000,
        "m": False,
        "M": True,
    }
    base.update(overrides)
    return base


def _depth_fixture(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "e": "depthUpdate",
        "E": 1700000000000,
        "s": "BTCUSDT",
        "b": [["60000.00", "0.01000000"], ["59999.00", "0.02000000"]],
        "a": [["60001.00", "0.01500000"], ["60002.00", "0.03000000"]],
    }
    base.update(overrides)
    return base


def _24hr_fixture(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "e": "24hrTicker",
        "E": 1700000000000,
        "s": "BTCUSDT",
        "p": "1200.00",
        "P": "2.00",
        "w": "60000.00",
        "b": "59900.00",
        "B": "0.01",
        "a": "60100.00",
        "A": "0.02",
        "h": "61000.00",
        "l": "59000.00",
        "v": "1000.00000000",
        "q": "60000000.00000000",
    }
    base.update(overrides)
    return base


def _listen_key_fixture(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "listenKey": "abc123def456",
    }
    base.update(overrides)
    return base


# ============================================================================
# 정상 체결 메시지 파싱 (ticker/stream)
# ============================================================================


def test_parse_trade_message_returns_ticker():
    """정상 trade 메시지 → Ticker 반환."""
    msg = _trade_fixture()
    ticker = parse_binance_trade_message(msg)
    assert isinstance(ticker, Ticker)
    assert ticker.symbol == "BTC/USDT"
    assert ticker.exchange == "binance"
    assert ticker.price == Decimal("60000.00")
    assert ticker.volume_24h == Decimal("0.01000000")


def test_parse_trade_message_bid_ask_equal_price():
    """trade 단일 메시지에 bid/ask 없음 — price로 동일하게 설정."""
    msg = _trade_fixture(p="61000.50")
    ticker = parse_binance_trade_message(msg)
    assert ticker.bid == ticker.price
    assert ticker.ask == ticker.price


# ============================================================================
# 정상 호가 메시지 파싱 (orderbook)
# ============================================================================


def test_parse_orderbook_message_returns_levels():
    """정상 depthUpdate 메시지 → OrderBook 반환, bids/asks 정확히 매핑."""
    msg = _depth_fixture()
    ob = parse_binance_orderbook_message(msg)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC/USDT"
    assert ob.exchange == "binance"
    assert len(ob.bids) == 2
    assert len(ob.asks) == 2
    assert ob.bids[0].price == Decimal("60000.00")
    assert ob.bids[0].quantity == Decimal("0.01000000")
    assert ob.asks[0].price == Decimal("60001.00")
    assert ob.asks[0].quantity == Decimal("0.01500000")


def test_parse_orderbook_message_empty_returns_none():
    """bids/asks 모두 빈 배열 → None (스냅샷 아님)."""
    msg = _depth_fixture(b=[], a=[])
    ob = parse_binance_orderbook_message(msg)
    assert ob is None


def test_parse_orderbook_message_with_symbol_arg():
    """symbol 인자 전달 시 메시지 내부 "s" 대신 그 값을 사용."""
    msg = _depth_fixture()
    ob = parse_binance_orderbook_message(msg, symbol="ETHUSDT")
    assert ob.symbol == "ETH/USDT"


# ============================================================================
# 정상 24hrTicker 메시지 파싱
# ============================================================================


def test_parse_24hr_ticker_message():
    """정상 24hrTicker 메시지 → Ticker 반환, 가중평균을 price로 사용."""
    msg = _24hr_fixture()
    ticker = parse_binance_24hr_ticker_message(msg)
    assert isinstance(ticker, Ticker)
    assert ticker.symbol == "BTC/USDT"
    assert ticker.price == Decimal("60000.00")
    assert ticker.bid == Decimal("59900.00")
    assert ticker.ask == Decimal("60100.00")
    assert ticker.volume_24h == Decimal("1000.00000000")


# ============================================================================
# negative tests — malformed 메시지 (>= 3건, D2 floor)
# ============================================================================


def test_parse_trade_missing_price_raises():
    """부정 테스트 1: trade 메시지 "p"(가격) 필드 누락 → FatalExchangeError."""
    msg = _trade_fixture()
    del msg["p"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_binance_trade_message(msg)


def test_parse_trade_missing_symbol_raises():
    """부정 테스트 2: trade 메시지 "s"(심볼) 필드 누락 → FatalExchangeError."""
    msg = _trade_fixture()
    del msg["s"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_binance_trade_message(msg)


def test_parse_trade_missing_timestamp_raises():
    """부정 테스트 3: trade 메시지 "T"(체결 시간) 필드 누락 → FatalExchangeError."""
    msg = _trade_fixture()
    del msg["T"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_binance_trade_message(msg)


def test_parse_orderbook_missing_bids_raises():
    """부정 테스트 4: orderbook 메시지 "b"(bid) 필드 누락 → FatalExchangeError."""
    msg = _depth_fixture()
    del msg["b"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_binance_orderbook_message(msg)


def test_parse_24hr_missing_weighted_avg_raises():
    """부정 테스트 5: 24hrTicker 메시지 "w"(가중평균) 필드 누락 → FatalExchangeError."""
    msg = _24hr_fixture()
    del msg["w"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_binance_24hr_ticker_message(msg)


def test_parse_listen_key_missing_raises():
    """부정 테스트 6: listenKey 응답에 listenKey 필드 누락 → FatalExchangeError."""
    msg = _listen_key_fixture()
    del msg["listenKey"]
    with pytest.raises(FatalExchangeError, match="필드 누락"):
        parse_listen_key_response(msg)


# ============================================================================
# failure injection — 비정상 입력
# ============================================================================


def test_parse_trade_rejects_non_dict_input():
    """장애주입: dict가 아닌 입력(리스트) → TypeError 또는 FatalExchangeError."""
    with pytest.raises((FatalExchangeError, TypeError, KeyError)):
        # Passing a list instead of dict — KeyError from message["s"] or TypeError
        parse_binance_trade_message(["not", "a", "dict"])


def test_parse_orderbook_rejects_invalid_price_format():
    """장애주입: 호가 가격에 NaN 문자열 → Pydantic ValidationError."""
    from pydantic import ValidationError

    msg = _depth_fixture(b=[["NaN", "0.01"]], a=[])
    with pytest.raises(ValidationError):
        parse_binance_orderbook_message(msg)


# ============================================================================
# 이벤트 분류
# ============================================================================


def test_classify_trade_event():
    msg = _trade_fixture()
    assert classify_binance_ws_event(msg) == "trade"


def test_classify_depth_event():
    msg = _depth_fixture()
    assert classify_binance_ws_event(msg) == "depthUpdate"


def test_classify_24hr_event():
    msg = _24hr_fixture()
    assert classify_binance_ws_event(msg) == "24hrTicker"


def test_classify_unknown_event():
    msg = {"e": "unknownEventType"}
    assert classify_binance_ws_event(msg) == "unknown"


def test_classify_empty_event():
    msg = {}
    assert classify_binance_ws_event(msg) == "control"


# ============================================================================
# listenKey 파싱
# ============================================================================


def test_parse_listen_key_returns_string():
    key = parse_listen_key_response(_listen_key_fixture())
    assert isinstance(key, str)
    assert key == "abc123def456"


# ============================================================================
# 성능 수치 단언 (D2 floor)
# ============================================================================


@pytest.mark.perf
def test_parse_trade_performance_budget():
    """수치 성능 단언: trade 메시지 파싱 1000회 호출 평균 100μs 미만."""
    msg = _trade_fixture()
    start = time.perf_counter()
    for _ in range(1000):
        parse_binance_trade_message(msg)
    elapsed = time.perf_counter() - start
    assert elapsed / 1000 < 0.0001
