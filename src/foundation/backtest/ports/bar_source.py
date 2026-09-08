"""L26 -- PointInTimeBars: bar_index 시점까지만 노출하는 bar 접근 계약.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (ports/bar_source.py 행).
구현체(adapters/list_bars.py)는 재생 가능한 범위 밖 인덱스 접근을
`BACKTEST_LOOKAHEAD_VIOLATION`으로 거부해야 한다(I2).

Protocol 선언과 `...` 본문만 둔다 -- I/O·로깅·기본 구현 금지(L26 DoD (b)).
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
