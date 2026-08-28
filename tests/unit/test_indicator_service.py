"""14.1 단위테스트 — 각 지표군 최소 1개씩 TA-Lib 참조값과 일치 검증(완료조건)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import talib

from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle


def _candles(n: int, *, base=100.0, volume=1000.0) -> list[Candle]:
    now = datetime.now(timezone.utc)
    out = []
    for i in range(n):
        price = base + i
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal(str(volume)),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def _closes(candles) -> np.ndarray:
    return np.array([float(c.close) for c in candles], dtype=np.float64)


def test_trend_group_sma_matches_talib_reference():
    candles = _candles(30)
    result = IndicatorService().calculate("SMA", candles, timeperiod=5)

    expected = talib.SMA(_closes(candles), timeperiod=5)
    assert result.values[-1] == expected[-1]


def test_trend_group_macd_matches_talib_reference():
    candles = _candles(60)
    result = IndicatorService().calculate("MACD", candles)

    expected_macd, expected_signal, expected_hist = talib.MACD(_closes(candles))
    assert result.values[-1] == expected_macd[-1]
    assert result.series["signal"][-1] == expected_signal[-1]
    assert result.series["hist"][-1] == expected_hist[-1]


def test_momentum_group_rsi_matches_talib_reference():
    candles = _candles(30)
    result = IndicatorService().calculate("RSI", candles, timeperiod=14)

    expected = talib.RSI(_closes(candles), timeperiod=14)
    assert result.values[-1] == expected[-1]


def test_volatility_group_atr_matches_talib_reference():
    candles = _candles(30)
    service = IndicatorService()
    result = service.calculate("ATR", candles, timeperiod=14)

    arrays_high = np.array([float(c.high) for c in candles], dtype=np.float64)
    arrays_low = np.array([float(c.low) for c in candles], dtype=np.float64)
    expected = talib.ATR(arrays_high, arrays_low, _closes(candles), timeperiod=14)
    assert result.values[-1] == expected[-1]


def test_volatility_group_bbands_returns_three_series():
    candles = _candles(20)
    result = IndicatorService().calculate("BBANDS", candles, timeperiod=5)

    assert result.series is not None
    assert set(result.series) == {"upperband", "middleband", "lowerband"}
    assert result.values == result.series["upperband"]


def test_volume_group_obv_matches_talib_reference():
    candles = _candles(20)
    service = IndicatorService()
    result = service.calculate("OBV", candles)

    volumes = np.array([float(c.volume) for c in candles], dtype=np.float64)
    expected = talib.OBV(_closes(candles), volumes)
    assert result.values[-1] == expected[-1]


def test_insufficient_candles_returns_empty_with_message():
    candles = _candles(3)

    result = IndicatorService().calculate("SMA", candles, timeperiod=200)

    assert result.values == []
    assert "데이터 부족" in result.message


def test_unsupported_indicator_raises():
    import pytest

    from src.core.indicators.talib_adapter import IndicatorError

    with pytest.raises(IndicatorError):
        IndicatorService().calculate("ICHIMOKU", _candles(30))
