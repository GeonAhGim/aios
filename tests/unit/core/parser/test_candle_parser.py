"""Bitget v2 실제 라이브 응답(2026-08-28,
GET /api/v2/spot/market/candles?symbol=BTCUSDT&granularity=1min) 캡처본을
fixture로 사용."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.exceptions import FatalExchangeError
from src.core.parser.candle_parser import parse_candles

REAL_BITGET_CANDLES = [
    ["1787853000000", "80515", "80565", "80510", "80565", "2.009698",
     "161876.22972749", "161876.22972749"],
    ["1787853060000", "80565", "80565", "80554.67", "80554.67", "0.094913",
     "7646.65714303", "7646.65714303"],
]


def test_parse_candles_from_real_bitget_response():
    candles = parse_candles(REAL_BITGET_CANDLES, "bitget", "BTC/USDT", "1m")
    assert len(candles) == 2
    first = candles[0]
    assert first.symbol == "BTC/USDT"
    assert first.open == Decimal("80515")
    assert first.high == Decimal("80565")
    assert first.low == Decimal("80510")
    assert first.close == Decimal("80565")
    assert first.volume == Decimal("2.009698")
    assert first.open_time == datetime.fromtimestamp(1787853000000 / 1000, tz=timezone.utc)
    assert first.close_time == first.open_time + timedelta(minutes=1)


def test_parse_candles_unknown_timeframe_raises():
    with pytest.raises(FatalExchangeError):
        parse_candles(REAL_BITGET_CANDLES, "bitget", "BTC/USDT", "3m")


def test_parse_candles_unsupported_exchange_raises():
    with pytest.raises(FatalExchangeError):
        parse_candles(REAL_BITGET_CANDLES, "kis", "BTC/USDT", "1m")
