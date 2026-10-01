"""14.1 — Technical indicator library integration (IndicatorService).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L03,
docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-10

A pure computation layer that applies TA-Lib to candle data (FD-2 pipeline
output, Candle model) to calculate time-series indicator values — does not
cross the FD-8 (FROZEN, actual trading decision) boundary. Parameter
validation and lookback computation are fully delegated to L01/L02
(`specs_talib.py`/`registry.py`) — the defect in §1 (the old per-indicator
spec dict allowed timeperiod=0 and negative values due to missing range
validation, and lookback relied on only a single period parameter, causing
mismatches with the actual bar requirements for MACD/BBANDS/STOCH) is
fixed here by replacing it with `DEFAULT_REGISTRY` rather than
re-implementing the logic.

After IND-10 (161 indicators auto-generated), `SUPPORTED_INDICATORS`
reflects the full `TALIB_SPECS` (150 auto-generated + 11 manual overrides)
rather than a hand-listed set of 11. `INDICATOR_GROUPS` is a read-only view
grouping indicators by TA-Lib's own 10 groups (`TALIB_GROUPS`) — the old
TREND/MOMENTUM/VOLATILITY/VOLUME 4-classification was too coarse to describe
161 indicators (e.g., Momentum Indicators alone contains 31 types).

Scope reduction: Ichimoku, Keltner Channel, VWAP, Fibonacci Retracement,
and Pivot Points are not part of TA-Lib's standard function set (they
require separate implementations with proprietary formulas) — these
indicators cannot be validated against the completion requirement of
"matching TA-Lib reference values", so they are not covered in this leaf.
`MAVP` is a TA-Lib standard function, but its second input is a variable
period array (`periods`), not a candle field, so a `Candle`-based adapter
cannot supply it — it is registered in the registry but rejected at
`calculate()` call time with `STRATEGY_INDICATOR_INPUT_UNSUPPORTED`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import numpy as np
import talib
from pydantic import BaseModel

from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError
from src.core.indicators.spec import REGISTRY_VERSION
from src.core.indicators.specs_talib import TALIB_GROUPS, TALIB_SPECS
from src.data.models.market_data import Candle

SUPPORTED_INDICATORS = tuple(sorted(TALIB_SPECS))


def _group_indicators() -> dict[str, tuple[str, ...]]:
    by_group: dict[str, list[str]] = defaultdict(list)
    for name in SUPPORTED_INDICATORS:
        by_group[TALIB_GROUPS[name]].append(name)
    return {group: tuple(sorted(names)) for group, names in by_group.items()}


INDICATOR_GROUPS = _group_indicators()

__all__ = [
    "INDICATOR_GROUPS",
    "SUPPORTED_INDICATORS",
    "IndicatorError",
    "IndicatorResult",
    "IndicatorService",
]


class IndicatorResult(BaseModel):
    indicator: str
    values: list[float | None]
    series: dict[str, list[float | None]] | None = None
    params: dict[str, int]
    message: str | None = None
    registry_version: str = REGISTRY_VERSION


def _candle_arrays(candles: Sequence[Candle]) -> dict[str, np.ndarray[Any, Any]]:
    return {
        "open": np.array([float(c.open) for c in candles], dtype=np.float64),
        "high": np.array([float(c.high) for c in candles], dtype=np.float64),
        "low": np.array([float(c.low) for c in candles], dtype=np.float64),
        "close": np.array([float(c.close) for c in candles], dtype=np.float64),
        "volume": np.array([float(c.volume) for c in candles], dtype=np.float64),
    }


def _clean(arr: np.ndarray[Any, Any]) -> list[float | None]:
    """Clean TA-Lib output to a list of None (missing)/float values.

    61 candle pattern (CDL*) indicators return int32 arrays; `np.isnan`
    cannot be applied to integer dtypes (TypeError) — cast to float64
    first before processing (integer arrays cannot hold NaN, so no values
    are lost).
    """
    floats = arr.astype(np.float64, copy=False)
    return [None if np.isnan(v) else float(v) for v in floats]


class IndicatorService:
    """Candles → indicator values. Delegates indicator lookup, parameter
    validation, and lookback to `DEFAULT_REGISTRY` (L02); here we only
    handle TA-Lib calls and result wrapping."""

    def calculate(
        self, indicator: str, candles: Sequence[Candle], **params: int
    ) -> IndicatorResult:
        spec = DEFAULT_REGISTRY.get(indicator)
        resolved_params = DEFAULT_REGISTRY.validate_params(indicator, params)
        min_required = DEFAULT_REGISTRY.lookback(indicator, resolved_params) + 1

        if len(candles) < min_required:
            return IndicatorResult(
                indicator=indicator,
                values=[],
                params=resolved_params,
                message=f"데이터 부족, 최소 {min_required}개 필요",
            )

        arrays = _candle_arrays(candles)
        missing_inputs = [name for name in spec.inputs if name not in arrays]
        if missing_inputs:
            raise IndicatorError("STRATEGY_INDICATOR_INPUT_UNSUPPORTED")
        inputs = [arrays[name] for name in spec.inputs]
        talib_func = getattr(talib, indicator)
        raw_output = talib_func(*inputs, **resolved_params)

        if len(spec.outputs) == 1:
            return IndicatorResult(
                indicator=indicator, values=_clean(raw_output), params=resolved_params
            )

        series = {name: _clean(line) for name, line in zip(spec.outputs, raw_output, strict=True)}
        primary = series[spec.outputs[0]]
        return IndicatorResult(
            indicator=indicator, values=primary, series=series, params=resolved_params
        )
