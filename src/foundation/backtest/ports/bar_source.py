"""L26 -- PointInTimeBars: a bar-access contract that exposes bars only up
to bar_index.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (ports/bar_source.py row).
The implementation (adapters/list_bars.py) must reject access to an index
outside the replayable range with `BACKTEST_LOOKAHEAD_VIOLATION` (I2).

Only the Protocol declaration and `...` bodies belong here -- no I/O,
logging, or default implementation (L26 DoD (b)).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from src.data.models.market_data import Candle

__all__ = ["PointInTimeBars"]


@runtime_checkable
class PointInTimeBars(Protocol):
    def upto(self, bar_index: int) -> Sequence[Candle]: ...
    def at(self, bar_index: int) -> Candle: ...
    def __len__(self) -> int: ...
