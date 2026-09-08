"""BT-16a (2/3) — `backtest/vector/walk_forward.py`: rolling train/test window execution.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. task-2371 decision -- same split as the `grid.py` module docstring (the AI-10
experiment-ledger record is deferred to BT-16b, this leaf is execution-only).

Walk-forward divides the data into consecutive (train window, test window) pairs; for
each window it picks the best combo on the train window, then scores that combo only on
the same window's test window (the portion training never saw) -- the logic that picks a
combo from train-window performance is itself the core of this leaf ("execution part",
not an optimization-algorithm leaf: the candidate combos and their signals are still
built and handed in by the caller). The selection metric is
`QuickBacktestResult.final_equity` (other metrics such as Sharpe are out of scope for
this leaf -- if needed, a separate leaf will address them explicitly; not built now on
speculation).

It does not reimplement the fill arithmetic (I-05) -- like `grid.py`, it calls BT-15b
`run_vector_backtest` unchanged for every window/combo pair. Splitting into windows is
just array slicing over `CandleColumns`/`VectorSignal` (same approach as the BT-11
`deep_backtest_job._prefix` precedent -- no new time-series representation introduced).

Pure module -- no I/O.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "WalkForwardError",
    "WalkForwardResult",
    "WalkForwardWindow",
    "WalkForwardWindowSpec",
    "run_walk_forward",
]


class WalkForwardError(ValueError):
    """`BT_VECTOR_WALK_FORWARD` — fail-closed rejection for no combos, window size
    larger than the data, and the like."""


@dataclass(frozen=True, slots=True)
class WalkForwardWindowSpec:
    """Picks the combo over `train_bars` bars, then scores it over the immediately
    following `test_bars` bars. Advances the next window's train start by `step_bars`
    (defaults to `test_bars` when omitted, i.e. test windows are back-to-back with no
    overlap by default)."""

    train_bars: int
    test_bars: int
    step_bars: int | None = None

    def __post_init__(self) -> None:
        if self.train_bars <= 0:
            raise WalkForwardError(f"train_bars는 양수여야 한다: {self.train_bars}")
        if self.test_bars <= 0:
            raise WalkForwardError(f"test_bars는 양수여야 한다: {self.test_bars}")
        if self.step_bars is not None and self.step_bars <= 0:
            raise WalkForwardError(f"step_bars는 양수여야 한다: {self.step_bars}")

    @property
    def effective_step_bars(self) -> int:
        return self.step_bars if self.step_bars is not None else self.test_bars


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """`[train_start, train_end)` is the training window used to select the combo;
    `[test_start, test_end)` (always `test_start == train_end`) is the test window that
    scored the selected `selected_combo`."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int
    selected_combo: str
    train_result: QuickBacktestResult
    test_result: QuickBacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    windows: tuple[WalkForwardWindow, ...]


def run_walk_forward(
    columns: CandleColumns,
    combos: Mapping[str, VectorSignal],
    config: BacktestConfigV2,
    window: WalkForwardWindowSpec,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> WalkForwardResult:
    """Splits all of `columns` sequentially into (train, test) windows per the `window`
    spec; for each window, runs every combo in `combos` over the train segment, picks
    the one with the largest `final_equity`, then runs it again over the test segment.

    If not a single window can be formed (the data is shorter than
    `train_bars+test_bars`), this rejects rather than silently returning an empty
    result -- a "zero-window walk-forward" signals that the caller misconfigured the
    window spec or data range, not a normal empty result."""
    if not combos:
        raise WalkForwardError("combos가 비어 있다 — 훈련 구간에서 고를 조합이 없다")

    n = len(columns)
    step = window.effective_step_bars
    windows: list[WalkForwardWindow] = []
    train_start = 0
    while True:
        train_end = train_start + window.train_bars
        test_end = train_end + window.test_bars
        if test_end > n:
            break

        train_columns = _slice_columns(columns, train_start, train_end)
        train_results = {
            key: run_vector_backtest(
                config, train_columns, _slice_signal(signal, train_start, train_end),
                timeframe=timeframe, initial_cash=initial_cash, funding_rate=funding_rate,
            )
            for key, signal in combos.items()
        }
        best_key = max(train_results, key=lambda k: train_results[k].final_equity)

        test_columns = _slice_columns(columns, train_end, test_end)
        test_result = run_vector_backtest(
            config, test_columns, _slice_signal(combos[best_key], train_end, test_end),
            timeframe=timeframe, initial_cash=initial_cash, funding_rate=funding_rate,
        )
        windows.append(WalkForwardWindow(
            train_start=train_start, train_end=train_end,
            test_start=train_end, test_end=test_end,
            selected_combo=best_key,
            train_result=train_results[best_key], test_result=test_result,
        ))
        train_start += step

    if not windows:
        raise WalkForwardError(
            f"창을 하나도 만들 수 없다 — train_bars+test_bars="
            f"{window.train_bars + window.test_bars}가 캔들 수 {n}보다 크다"
        )
    return WalkForwardResult(windows=tuple(windows))


def _slice_columns(columns: CandleColumns, start: int, end: int) -> CandleColumns:
    """Manual per-field slicing, same approach as BT-11 `deep_backtest_job._prefix` --
    `CandleColumns` is a pure data-holder dataclass with no slicing helper."""
    return CandleColumns(
        ts=columns.ts[start:end], open=columns.open[start:end], high=columns.high[start:end],
        low=columns.low[start:end], close=columns.close[start:end],
        volume=columns.volume[start:end], quote_volume=columns.quote_volume[start:end],
    )


def _slice_signal(signal: VectorSignal, start: int, end: int) -> VectorSignal:
    return VectorSignal(
        entries=BoolSignal(
            values=signal.entries.values[start:end], na=signal.entries.na[start:end],
        ),
        exits=BoolSignal(
            values=signal.exits.values[start:end], na=signal.exits.na[start:end],
        ),
        quantity=signal.quantity,
    )
