"""BR-9(task-1787) — `src/exchanges/bitget/parsers.py`(구 `src/core/parser/*`)
이동 후 회귀 테스트. Bitget v2 실제 라이브 응답 캡처본을 fixture로 사용
(FD-2.5 완료조건: "실제 Bitget 응답 샘플 기준 단위테스트").

구 테스트(`tests/unit/core/parser/test_*.py`)의 "지원하지 않는 거래소"
negative test는 삭제한다 — `exchange` 매개변수 자체가 없어져 그 실패
경로가 더 이상 존재하지 않는다(ADR-2026-09-06-I D5, 상위 계층 문자열 가드
제거가 이 리프의 목적).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.bitget.parsers import parse_candles, parse_orderbook, parse_ticker

REAL_BITGET_TICKER = {
    "open": "78217.08",
    "symbol": "BTCUSDT",
    "high24h": "80800",
    "low24h": "78196",
    "lastPr": "80663.08",
    "quoteVolume": "270635812.383435",
    "baseVolume": "3407.420693",
    "usdtVolume": "270635812.38343424",
    "ts": "1787851009318",
    "bidPr": "80664.02",
    "askPr": "80664.03",
    "bidSz": "0.859943",
    "askSz": "0.29158",
    "openUtc": "79023.47",
    "changeUtc24h": "0.02075",
    "change24h": "0.03127",
}

REAL_BITGET_ORDERBOOK = {
    "asks": [
        ["80565", "1.3861510000000000"],
        ["80568.63", "0.0297160000000000"],
    ],
    "bids": [
        ["80564.99", "0.3894280000000000"],
        ["80562.93", "0.0001860000000000"],
    ],
    "ts": "1787853071676",
}

REAL_BITGET_CANDLES = [
    ["1787853000000", "80515", "80565", "80510", "80565", "2.009698",
     "161876.22972749", "161876.22972749"],
    ["1787853060000", "80565", "80565", "80554.67", "80554.67", "0.094913",
     "7646.65714303", "7646.65714303"],
]


def test_parse_ticker_from_real_bitget_response():
    ticker = parse_ticker(REAL_BITGET_TICKER)
    assert ticker.symbol == "BTC/USDT"
    assert ticker.exchange == "bitget"
    assert ticker.price == Decimal("80663.08")
    assert ticker.bid == Decimal("80664.02")
    assert ticker.ask == Decimal("80664.03")
    assert ticker.volume_24h == Decimal("3407.420693")
    assert ticker.source_type == "primary"


def test_parse_ticker_source_type_override():
    ticker = parse_ticker(REAL_BITGET_TICKER, source_type="reference")
    assert ticker.source_type == "reference"


def test_parse_ticker_missing_field_raises_fatal_not_silent_default():
    broken = dict(REAL_BITGET_TICKER)
    del broken["lastPr"]
    with pytest.raises(FatalExchangeError):
        parse_ticker(broken)


def test_parse_orderbook_from_real_bitget_response():
    book = parse_orderbook(REAL_BITGET_ORDERBOOK, "BTC/USDT")
    assert book.symbol == "BTC/USDT"
    assert book.exchange == "bitget"
    assert book.asks[0].price == Decimal("80565")
    assert book.asks[0].quantity == Decimal("1.3861510000000000")
    assert book.bids[0].price == Decimal("80564.99")
    assert book.bids[0].price > book.bids[1].price  # 내림차순(최우선 매수호가가 첫 행)
    assert book.asks[0].price < book.asks[1].price  # 오름차순(최우선 매도호가가 첫 행)


def test_parse_orderbook_missing_field_raises():
    broken = {"asks": REAL_BITGET_ORDERBOOK["asks"], "bids": REAL_BITGET_ORDERBOOK["bids"]}
    with pytest.raises(FatalExchangeError):
        parse_orderbook(broken, "BTC/USDT")


def test_parse_candles_from_real_bitget_response():
    candles = parse_candles(REAL_BITGET_CANDLES, "BTC/USDT", "1m")
    assert len(candles) == 2
    first = candles[0]
    assert first.symbol == "BTC/USDT"
    assert first.exchange == "bitget"
    assert first.open == Decimal("80515")
    assert first.high == Decimal("80565")
    assert first.low == Decimal("80510")
    assert first.close == Decimal("80565")
    assert first.volume == Decimal("2.009698")
    assert first.open_time == datetime.fromtimestamp(1787853000000 / 1000, tz=timezone.utc)
    assert first.close_time == first.open_time + timedelta(minutes=1)


def test_parse_candles_unknown_timeframe_raises():
    with pytest.raises(FatalExchangeError):
        parse_candles(REAL_BITGET_CANDLES, "BTC/USDT", "3m")
