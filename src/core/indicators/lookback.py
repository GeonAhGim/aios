"""L07 -- per-timeframe required-bar counts for indicator keys.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #section9 L07.

Pure leaf -- delegates the lookback arithmetic to
`IndicatorRegistry.lookback` (L02, `src/core/indicators/registry.py`) and key
parsing to `parse_key` (L06, `src/core/strategy/indicator_key.py`). This
module only adds the "+1 warm-up bar" convention (the current bar itself,
on top of the prior bars the indicator consumes) and the per-timeframe max
aggregation across several indicator keys.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from src.core.indicators.registry import IndicatorRegistry
from src.core.strategy.indicator_key import parse_key


def indicator_required_bars(
    name: str, params: Mapping[str, int], registry: IndicatorRegistry
) -> int:
    """Bars needed before `name(params)` produces its first value.

    `registry.lookback` returns the count of *prior* bars an indicator
    consumes (e.g. RSI(14) needs 14 prior closes); the bar on which the
    first value lands is the +1. Unknown `name` or out-of-range `params`
    propagate as `IndicatorError` from the registry (fail-closed).
    """
    return registry.lookback(name, params) + 1


def required_bars(keys: Iterable[str], registry: IndicatorRegistry) -> dict[str, int]:
    """Per-timeframe max `indicator_required_bars` across `keys`.

    Every key must carry an explicit `@timeframe` suffix (L06 grammar) --
    there is no sane default timeframe to bucket an untagged key under, so
    a missing one is rejected rather than silently defaulted. When several
    keys share a timeframe, that timeframe's entry is the *maximum* of
    their required-bar counts (they share one time axis, so the slowest
    indicator sets the floor -- summing or averaging would either starve
    the other indicators of history or warm up longer than necessary).
    """
    result: dict[str, int] = {}
    for key in keys:
        parsed = parse_key(key)
        if parsed.timeframe is None:
            raise ValueError(f"required_bars: key has no @timeframe: {key!r}")
        bars = indicator_required_bars(parsed.indicator, parsed.params, registry)
        result[parsed.timeframe] = max(result.get(parsed.timeframe, 0), bars)
    return result


__all__ = ["indicator_required_bars", "required_bars"]
