"""L26 -- ListBars: `PointInTimeBars`의 리스트 기반 구현.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (adapters/list_bars.py 행).

범위 밖 인덱스(아직 재생되지 않은 미래 bar, 음수 인덱스)는 Python 기본
`IndexError`나 음수 인덱스의 "끝에서부터" 관례로 조용히 통과시키지 않는다
-- 재생 루프가 실수로 미래 bar를 참조하면 반드시 `BACKTEST_LOOKAHEAD_VIOLATION`
으로 fail-closed 해야 한다(I2, 109번 §5 look-ahead bias 방지). 이 파일이
그 에러 코드 문자열의 단일 출처다 -- 다른 곳에서 같은 문자열을 재정의하지
말 것.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from src.data.models.market_data import Candle

__all__ = ["BacktestLookaheadError", "ListBars"]


class BacktestLookaheadError(ValueError):
    """`BACKTEST_LOOKAHEAD_VIOLATION`(422) -- 재생 가능한 범위 밖의
    `bar_index`를 참조했다. 재시도가 아니라 호출자의 `bar_index` 계산을
    고쳐야 한다."""

    error_code: ClassVar[str] = "BACKTEST_LOOKAHEAD_VIOLATION"

    def __init__(self, *, bar_index: int, length: int) -> None:
        self.bar_index = bar_index
        self.length = length
        super().__init__(
            f"{self.error_code}: bar_index={bar_index} is outside the "
            f"replayable range [0, {length})"
        )


class ListBars:
    """`bars`를 고정 시퀀스로 감싸 point-in-time 접근만 허용한다."""

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
