"""L06 — indicator key grammar single source of truth.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 62 (indicator_key.py).
Grammar: `{INDICATOR}{_paramName<int>}*[.output][@timeframe]`, e.g.
`RSI_timeperiod14@1h`, `MACD_slowperiod26.signal`, `SMA_timeperiod20`.
`market_state.py:_KEY_RE` and `condition_evaluator.extract_indicator_keys` are
replaced by this module (spec row 62/65) so the grammar has exactly one owner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Mirrors src.foundation.market_data.contracts.v1_enums.Timeframe values. Kept
# as a local constant (not imported) so this FROZEN_PAPER_ONLY leaf does not
# take on a SCAFFOLD-zone dependency; tests assert set equality against
# Timeframe so the two cannot silently drift.
ALLOWED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d", "L2"})

_KEY_RE = re.compile(
    r"^(?P<indicator>[A-Z]+)"
    r"(?P<params>(?:_[a-z]+\d+)*)"
    r"(?:\.(?P<output>[a-z]+))?"
    r"(?:@(?P<timeframe>[^@]+))?$"
)
_PARAM_RE = re.compile(r"_([a-z]+)(\d+)")


class IndicatorKeyError(Exception):
    """Key does not match the grammar, or names a timeframe outside `ALLOWED_TIMEFRAMES`."""


@dataclass(frozen=True)
class IndicatorKey:
    indicator: str
    params: dict[str, int] = field(default_factory=dict)
    output: str | None = None
    timeframe: str | None = None


def parse_key(key: str) -> IndicatorKey:
    """Parse an indicator key. Raises `IndicatorKeyError` for any malformed
    input, including an empty string and an `@` suffix with an empty value —
    both fail the same regex match, so they share one error path."""
    match = _KEY_RE.match(key)
    if match is None:
        raise IndicatorKeyError(f"malformed indicator key: {key!r}")
    timeframe = match["timeframe"]
    if timeframe is not None and timeframe not in ALLOWED_TIMEFRAMES:
        raise IndicatorKeyError(f"unsupported timeframe in key {key!r}: {timeframe!r}")
    params = {name: int(value) for name, value in _PARAM_RE.findall(match["params"])}
    return IndicatorKey(match["indicator"], params, match["output"], timeframe)


def format_key(key: IndicatorKey) -> str:
    """Inverse of `parse_key`: `format_key(parse_key(k)) == k` for any valid `k`."""
    text = key.indicator + "".join(f"_{name}{value}" for name, value in key.params.items())
    if key.output is not None:
        text += f".{key.output}"
    if key.timeframe is not None:
        text += f"@{key.timeframe}"
    return text


__all__ = ["ALLOWED_TIMEFRAMES", "IndicatorKey", "IndicatorKeyError", "format_key", "parse_key"]
