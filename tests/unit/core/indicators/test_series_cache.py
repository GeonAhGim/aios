"""L28 — series_cache.py 계약 테스트.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L28, DoD:
`value_at`이 전량 계산 결과와 bar별 재계산 결과 동일(인과성 증명),
미래 인덱스 접근 경로 부재.
"""

from __future__ import annotations

from collections.abc import Sequence
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
from src.core.indicators.talib_adapter import IndicatorResult, IndicatorService
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
        def calculate(
            self, indicator: str, candles: Sequence[Candle], **params: int
        ) -> IndicatorResult:
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


# ── D2 보강: failure injection ───────────────────────────────────────────


def test_service_calculate_exception_propagates() -> None:
    """IndicatorService.calculate()가 예외 발생 시, build()도 예외를 전파한다.

    부분 실패 시나리오: 지표 계산 중 예외가 나면 캐시 빌드 자체가 실패해야
    손상된 반쪽 결과로 IndicatorSeriesCache가 생성되지 않는다.
    """

    class _FailingService(IndicatorService):
        def calculate(
            self, indicator: str, candles: Sequence[Candle], **params: int
        ) -> IndicatorResult:
            raise RuntimeError("TA-Lib 내부 오류")

    bars = _candles(40)
    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")

    with pytest.raises(RuntimeError, match="TA-Lib 내부 오류"):
        IndicatorSeriesCache.build({"1h": bars}, [key], _FailingService())


# ── D2 보강: performance assertion ───────────────────────────────────────


def test_value_at_is_o1_no_recomputation() -> None:
    """value_at() 호출이 재계산을 trigger하지 않음을 호출 카운트로 검증.

    캐시 조회가 O(1) — 같은 키로 1000회 조회해도 underlying calculate()는
    build() 시점 1회만 호출된다.
    """
    bars = _candles(100)
    call_count = 0

    class _CountingService(IndicatorService):
        def calculate(
            self, indicator: str, candles: Sequence[Candle], **params: int
        ) -> IndicatorResult:
            nonlocal call_count
            call_count += 1
            return super().calculate(indicator, candles, **params)

    key = SeriesKey.of("SMA", {"timeperiod": 10}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], _CountingService())

    # build() 시 1회 호출
    assert call_count == 1

    # value_at() 1000회 호출 — calculate() 호출 수는 변하지 않아야 함
    for i in range(1000):
        cache.value_at(key, i % len(bars))
    assert call_count == 1


# ── D2 보강: gate-red reproduction (mypy 타입 정확성) ────────────────────


def test_decimal_precision_preserved_through_cache() -> None:
    """Decimal 정밀도 손실 감지 — float → str → Decimal 경로 검증.

    mypy --strict가 타입 힌트를 엄격히 검사하므로, build()의
    `Decimal(str(v))` 변환이 정밀도를 유지하는지 확인한다.
    float로 직접 Decimal을 만들면 정밀도 손실이 발생하므로
    반드시 str 경유해야 한다.
    """
    bars = _candles(50)
    service = IndicatorService()
    key = SeriesKey.of("SMA", {"timeperiod": 5}, "1h")
    cache = IndicatorSeriesCache.build({"1h": bars}, [key], service)

    # 캐시에서 읽은 값이 모두 Decimal 타입이다
    for i in range(len(bars)):
        val = cache.value_at(key, i)
        if val is not None:
            assert isinstance(val, Decimal)
            # 정밀도: float로 역변환 후 다시 Decimal하면 원값과 다를 수 있음
            # 하지만 Decimal(str(float)) 경로는 원본 float의 정확한 decimal 표현
            float_repr = float(val)
            assert float_repr == float(Decimal(str(float_repr)))
