"""Bitget v2 실제 라이브 응답(2026-08-28, GET /api/v2/spot/market/tickers?symbol=BTCUSDT)
캡처본을 fixture로 사용 — FD-2.5 완료조건("실제 Bitget 응답 샘플 기준 단위테스트")."""
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError
from src.core.parser.ticker_parser import parse_ticker

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


def test_parse_ticker_from_real_bitget_response():
    ticker = parse_ticker(REAL_BITGET_TICKER, "bitget")
    assert ticker.symbol == "BTC/USDT"
    assert ticker.exchange == "bitget"
    assert ticker.price == Decimal("80663.08")
    assert ticker.bid == Decimal("80664.02")
    assert ticker.ask == Decimal("80664.03")
    assert ticker.volume_24h == Decimal("3407.420693")
    assert ticker.source_type == "primary"


def test_parse_ticker_source_type_override():
    ticker = parse_ticker(REAL_BITGET_TICKER, "bitget", source_type="reference")
    assert ticker.source_type == "reference"


def test_parse_ticker_unsupported_exchange_raises():
    with pytest.raises(FatalExchangeError):
        parse_ticker(REAL_BITGET_TICKER, "kis")


def test_parse_ticker_missing_field_raises_fatal_not_silent_default():
    broken = dict(REAL_BITGET_TICKER)
    del broken["lastPr"]
    with pytest.raises(FatalExchangeError):
        parse_ticker(broken, "bitget")
