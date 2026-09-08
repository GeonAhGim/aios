"""L28 — one-time whole-range backtest computation + point-in-time lookup (removes O(n^2)).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L28

If strategy replay recomputes indicators from scratch on the candle slice up to each bar,
every time, that's O(n^2) (see the §1.2 gap citation, `run_backtest.py`'s
`bars[:bar_index+1]` recomputation). TA-Lib indicators are causal: index i's output is
determined only by elements 0..i of the input array, so computing once over the whole
bar array gives the same values as recomputing at every bar -- `build()` pins this
property in code by only accepting `causal=True` indicators.

`value_at` takes a single integer `bar_index`, not "the slice so far" -- since there is no
API at all to look up an index greater than bar_index, the look-ahead path is
structurally absent (an out-of-declared-range index is rejected fail-closed with
`SeriesCacheError`).
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
    """Cache build/lookup failure. `code` is for mapping errors in the upper layer."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SeriesKey:
    """Cache lookup key — identifies the indicator, params, timeframe, and output line.

    `params` is a sorted tuple, not a dict, so it's hashable (for use as a dict-cache
    key). Multi-output indicators (e.g. MACD) select a line via `output` (same name as
    the key in `IndicatorResult.series`) — omitting it uses the primary output line
    (`IndicatorResult.values`).
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
    """`SeriesKey` -> whole-range computation result (a `Decimal|None` tuple indexed
    by bar_index)."""

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
