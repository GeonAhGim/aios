"""L26 -- ListBars: a list-backed implementation of `PointInTimeBars`.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (adapters/list_bars.py row).

An out-of-range index (a future bar not yet replayed, or a negative index)
must not silently pass through via Python's default `IndexError` or the
negative-index "count from the end" convention -- if the replay loop
accidentally references a future bar, it must fail-closed with
`BACKTEST_LOOKAHEAD_VIOLATION` (I2, standard 109 §5 look-ahead bias
prevention). This file is the single source of truth for that error code
string -- do not redefine the same string elsewhere.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from src.data.models.market_data import Candle

__all__ = ["BacktestLookaheadError", "ListBars"]


class BacktestLookaheadError(ValueError):
    """`BACKTEST_LOOKAHEAD_VIOLATION` (422) -- referenced a `bar_index`
    outside the replayable range. Not retryable; the caller's `bar_index`
    computation must be fixed."""

    error_code: ClassVar[str] = "BACKTEST_LOOKAHEAD_VIOLATION"

    def __init__(self, *, bar_index: int, length: int) -> None:
        self.bar_index = bar_index
        self.length = length
        super().__init__(
            f"{self.error_code}: bar_index={bar_index} is outside the "
            f"replayable range [0, {length})"
        )


class ListBars:
    """Wraps `bars` as a fixed sequence, allowing only point-in-time access."""

    def __init__(self, bars: Sequence[Candle]) -> None:
        self._bars: tuple[Candle, ...] = tuple(bars)

    def __len__(self) -> int:
        return len(self._bars)

    def at(self, bar_index: int) -> Candle:
        self._check_bounds(bar_index)
        return self._bars[bar_index]

    def upto(self, bar_index: int) -> Sequence[Candle]:
        self._check_bounds(bar_index)
        return list(self._bars[: bar_index + 1])

    def _check_bounds(self, bar_index: int) -> None:
        if bar_index < 0 or bar_index >= len(self._bars):
            raise BacktestLookaheadError(bar_index=bar_index, length=len(self._bars))
