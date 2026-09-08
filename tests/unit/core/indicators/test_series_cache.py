"""L28 — series_cache.py 계약 테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L28, DoD:
`value_at`이 전량 계산 결과와 bar별 재계산 결과 동일(인과성 증명),
미래 인덱스 접근 경로 부재.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.core.indicators.registry import IndicatorRegistry
from src.core.indicators.series_cache import (
    IndicatorSeriesCache,
    SeriesCacheError,
    SeriesKey,
)
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle


def _candles(n: int, *, seed: int = 7) -> list[Candle]:
    rng = np.random.default_rng(seed)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    close = np.cumsum(rng.normal(size=n)) + 100.0
    out = []
    for i in range(n):
        price = float(close[i])
        out.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=Decimal("1000"),
                open_time=now + timedelta(hours=i),
                close_time=now + timedelta(hours=i + 1),
            )
        )
    return out


def test_value_at_matches_incremental_recomputation_sma() -> None:
    """인과성 증명: 전량 계산(value_at)과 매 bar `bars[:i+1]` 재계산이 동일."""
    bars = _candles(60)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)

    for i in range(len(bars)):
        incremental = service.calculate("SMA", bars[: i + 1], timeperiod=10).values
        expected = incremental[-1] if incremental else None
        actual = cache.value_at(key, i)
        if expected is None:
            assert actual is None
        else:
            assert actual == Decimal(str(expected))


def test_value_at_matches_incremental_recomputation_rsi() -> None:
    bars = _candles(80, seed=3)
    service = IndicatorService()
    key = SeriesKey.of("RSI", {"timeperiod": 14}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)

    for i in range(len(bars)):
        incremental = service.calculate("RSI", bars[: i + 1], timeperiod=14).values
        expected = incremental[-1] if incremental else None
        actual = cache.value_at(key, i)
        if expected is None:
            assert actual is None
        else:
            assert actual == Decimal(str(expected))


def test_multi_output_indicator_selects_named_line() -> None:
    bars = _candles(120, seed=11)
    service = IndicatorService()
    macd_key = SeriesKey.of("MACD", {}, "1h", output="macd")
    signal_key = SeriesKey.of("MACD", {}, "1h", output="signal")
    cache = IndicatorSeriesCache.build({"1h": bars}, [macd_key, signal_key], service)

    full = service.calculate("MACD", bars)
    assert full.series is not None
    last_idx = len(bars) - 1
    expected_macd = full.series["macd"][last_idx]
    expected_signal = full.series["signal"][last_idx]

    assert cache.value_at(macd_key, last_idx) == Decimal(str(expected_macd))
    assert cache.value_at(signal_key, last_idx) == Decimal(str(expected_signal))
    # 서로 다른 라인이므로 (일반적으로) 값이 다르다
    assert cache.value_at(macd_key, last_idx) != cache.value_at(signal_key, last_idx)


def test_default_output_is_primary_line() -> None:
    bars = _candles(120, seed=11)
    service = IndicatorService()
    key = SeriesKey.of("MACD", {}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)

    full = service.calculate("MACD", bars)
    last_idx = len(bars) - 1
    assert cache.value_at(key, last_idx) == Decimal(str(full.values[last_idx]))


def test_warmup_period_is_none() -> None:
    bars = _candles(30)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)
    assert cache.value_at(key, 0) is None
    assert cache.value_at(key, 8) is None
    assert cache.value_at(key, 9) is not None


def test_insufficient_bars_for_lookback_yields_all_none() -> None:
    bars = _candles(5)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 20}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)
    for i in range(len(bars)):
        assert cache.value_at(key, i) is None


def test_duplicate_keys_are_computed_once() -> None:
    bars = _candles(40)
    calls: list[str] = []

    class _CountingService(IndicatorService):
        def calculate(self, indicator: str, candles, **params):  # type: ignore[override]
            calls.append(indicator)
            return super().calculate(indicator, candles, **params)

    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    IndicatorSeriesCache.build({"1h": bars}, [key, key, key], _CountingService())
    assert calls == ["SMA"]


def test_unknown_key_raises() -> None:
    bars = _candles(40)
    service = IndicatorService()
    built_key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [built_key], service)

    other_key = SeriesKey.of("RSI", {"timeperiod": 14}, "1h")
    with pytest.raises(SeriesCacheError) as excinfo:
        cache.value_at(other_key, 0)
    assert excinfo.value.code == "STRATEGY_INDICATOR_KEY_UNKNOWN"


@pytest.mark.parametrize("bar_index", [-1, 40, 1000])
def test_out_of_range_bar_index_raises(bar_index: int) -> None:
    """미래(또는 음수) 인덱스 접근은 fail-closed 거부 — look-ahead 경로 부재."""
    bars = _candles(40)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)

    with pytest.raises(SeriesCacheError) as excinfo:
        cache.value_at(key, bar_index)
    assert excinfo.value.code == "STRATEGY_BAR_INDEX_OUT_OF_RANGE"


def test_missing_timeframe_raises() -> None:
    bars = _candles(40)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "4h")
    with pytest.raises(SeriesCacheError) as excinfo:
        IndicatorSeriesCache.build({"1h": bars}, [key], service)
    assert excinfo.value.code == "STRATEGY_TIMEFRAME_MISSING"


def test_unknown_output_line_raises() -> None:
    bars = _candles(120)
    service = IndicatorService()
    key = SeriesKey.of("MACD", {}, "1h", output="not_a_real_line")
    with pytest.raises(SeriesCacheError) as excinfo:
        IndicatorSeriesCache.build({"1h": bars}, [key], service)
    assert excinfo.value.code == "STRATEGY_INDICATOR_OUTPUT_UNKNOWN"


def test_noncausal_indicator_is_rejected_at_build() -> None:
    """causal=False 지표는 애초에 캐시에 올릴 수 없다(look-ahead 위험 자체 차단)."""
    noncausal_spec = IndicatorSpec(
        name="SMA",
        inputs=("close",),
        params=(ParamSpec(name="timeperiod", min=2, max=500, default=10),),
        outputs=("value",),
        lookback=lambda params: params["timeperiod"] - 1,
        plots=(PlotSpec(kind="line", scale="overlay", default_pane="price"),),
        causal=False,
    )
    registry = IndicatorRegistry({"SMA": noncausal_spec})
    bars = _candles(40)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")

    with pytest.raises(SeriesCacheError) as excinfo:
        IndicatorSeriesCache.build({"1h": bars}, [key], service, registry=registry)
    assert excinfo.value.code == "STRATEGY_INDICATOR_NONCAUSAL"


def test_series_key_of_normalizes_param_order() -> None:
    a = SeriesKey.of("SMA", {"timeperiod": 10, "extra": 1}, "1h")
    b = SeriesKey.of("SMA", {"extra": 1, "timeperiod": 10}, "1h")
    assert a == b
    assert hash(a) == hash(b)
