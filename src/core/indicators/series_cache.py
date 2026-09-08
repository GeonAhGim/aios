"""L28 — 백테스트 전구간 1회 계산 + point-in-time 조회(O(n^2) 제거).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L28

전략 리플레이가 매 bar마다 그때까지의 캔들 슬라이스로 지표를 처음부터
다시 계산하면 O(n^2)이 된다(§1.2 격차 인용, `run_backtest.py`의
`bars[:bar_index+1]` 재계산). TA-Lib 지표는 인덱스 i의 출력이 입력
배열의 0..i만으로 정해지는 인과적(causal) 계산이므로, 전체 bar 배열에
대해 딱 한 번 계산해도 매 bar 재계산과 값이 같다 — 이 성질을
`build()`가 `causal=True` 지표만 받도록 강제해 코드로 못박는다.

`value_at`은 "지금까지의 슬라이스"가 아니라 정수 `bar_index` 하나만
받는다 — bar_index보다 큰 인덱스를 조회할 API 자체가 없으므로 look-ahead
경로가 구조적으로 없다(선언한 인덱스 범위 밖은 `SeriesCacheError`로
fail-closed 거부).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle

__all__ = ["IndicatorSeriesCache", "SeriesCacheError", "SeriesKey"]


class SeriesCacheError(Exception):
    """캐시 구축/조회 실패. `code`는 상위 계층 오류 매핑용."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SeriesKey:
    """캐시 조회 키 — 지표·파라미터·타임프레임·출력 라인을 특정한다.

    `params`는 dict가 아니라 정렬된 튜플이라 해시 가능(딕셔너리 캐시의 키로
    쓰기 위함). 다중 출력 지표(MACD 등)는 `output`으로 라인을 고른다
    (`IndicatorResult.series`의 키와 같은 이름) — 생략하면 주 출력선
    (`IndicatorResult.values`)을 쓴다.
    """

    indicator: str
    params: tuple[tuple[str, int], ...]
    timeframe: str
    output: str | None = None

    @classmethod
    def of(
        cls,
        indicator: str,
        params: Mapping[str, int],
        timeframe: str,
        *,
        output: str | None = None,
    ) -> SeriesKey:
        return cls(
            indicator=indicator,
            params=tuple(sorted(params.items())),
            timeframe=timeframe,
            output=output,
        )


@dataclass(frozen=True, slots=True)
class IndicatorSeriesCache:
    """`SeriesKey` -> 전구간 계산 결과(bar_index로 색인하는 `Decimal|None` 튜플)."""

    _series: Mapping[SeriesKey, tuple[Decimal | None, ...]]

    @staticmethod
    def build(
        bars_by_tf: Mapping[str, Sequence[Candle]],
        keys: Sequence[SeriesKey],
        service: IndicatorService,
        *,
        registry: IndicatorRegistry = DEFAULT_REGISTRY,
    ) -> IndicatorSeriesCache:
        series: dict[SeriesKey, tuple[Decimal | None, ...]] = {}
        for key in keys:
            if key in series:
                continue
            spec = registry.get(key.indicator)
            if not spec.causal:
                raise SeriesCacheError("STRATEGY_INDICATOR_NONCAUSAL")
            try:
                bars = bars_by_tf[key.timeframe]
            except KeyError:
                raise SeriesCacheError("STRATEGY_TIMEFRAME_MISSING") from None

            result = service.calculate(key.indicator, bars, **dict(key.params))
            if key.output is None:
                values = result.values
            elif result.series is not None and key.output in result.series:
                values = result.series[key.output]
            else:
                raise SeriesCacheError("STRATEGY_INDICATOR_OUTPUT_UNKNOWN")

            n = len(bars)
            padded = values if len(values) == n else [None] * n
            series[key] = tuple(None if v is None else Decimal(str(v)) for v in padded)
        return IndicatorSeriesCache(series)

    def value_at(self, key: SeriesKey, bar_index: int) -> Decimal | None:
        try:
            values = self._series[key]
        except KeyError:
            raise SeriesCacheError("STRATEGY_INDICATOR_KEY_UNKNOWN") from None
        if bar_index < 0 or bar_index >= len(values):
            raise SeriesCacheError("STRATEGY_BAR_INDEX_OUT_OF_RANGE")
        return values[bar_index]
