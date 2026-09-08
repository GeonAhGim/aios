"""L35 -- `backtest/application/walk_forward.py`: per-split IS grid selection ->
OOS replay, then OOS-only stitched metrics.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L35 (contract: #2 table
`walk_forward.py` row). For each `Split`, runs every grid point over the split's
train (IS) bars, picks the point with the largest IS `sharpe_ratio` (net of costs --
`compute_metrics` already deducts fees/slippage), then replays only that point's
config over the split's test (OOS) bars -- exactly the vector-engine precedent
(`backtest/vector/walk_forward.py`: pick on train, score on test, never the reverse).
Ties keep the first point in `ParamGrid.points()` order, i.e. lexicographically-first
axis values (`ParamGrid.axes` requires each axis strictly ascending), because the
scan below only replaces the running best on a *strict* improvement.

`assert_no_overlap` re-validates the caller's `splits` before running anything --
`VALIDATION_OOS_LEAKAGE` must fail closed here too, not just at the `splits.py` call
site that first built them.

Each split is an independent replay from bar 0 of its own train/test slice (no
history carried across splits) -- the current `run_backtest` recomputes indicators
from bar 0 of whatever list it's given, so windows can't share indicator warmup
without leaking future bars into an earlier split's replay.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestMetrics,
    EquityPoint,
    SimulatedFill,
)
from src.foundation.backtest.domain.param_stability import ParamGrid
from src.foundation.backtest.domain.splits import Split, assert_no_overlap

__all__ = ["WalkForwardError", "WalkForwardReport", "WindowOutcome", "run_walk_forward"]

_SELECTION_RULE = "IS_NET_SHARPE_MAX"
_NEG_INF = Decimal("-Infinity")


class WalkForwardError(ValueError):
    """Fail-closed rejection -- empty `splits`, an unknown grid axis, or every IS
    candidate having an unscoreable (`None`) Sharpe."""


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    split: Split
    selected_params: dict[str, int]
    is_metrics: BacktestMetrics
    oos_metrics: BacktestMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    windows: tuple[WindowOutcome, ...]
    oos_stitched_metrics: BacktestMetrics
    selection_rule: str = _SELECTION_RULE


def run_walk_forward(
    base_config: BacktestConfig,
    fsm_config: FSMStrategyConfig,
    bars: list[Candle],
    grid: ParamGrid,
    splits: Sequence[Split],
    *,
    indicator_service: IndicatorService | None = None,
) -> WalkForwardReport:
    if not splits:
        raise WalkForwardError("splits가 비어 있다")
    assert_no_overlap(splits)
    axis_names = list(grid.axes.keys())
    unknown = [name for name in axis_names if name not in BacktestConfig.model_fields]
    if unknown:
        raise WalkForwardError(f"grid axes가 BacktestConfig 필드에 없다: {unknown}")

    windows: list[WindowOutcome] = []
    oos_curves: list[list[EquityPoint]] = []
    oos_fills: list[SimulatedFill] = []
    for split in splits:
        train_bars = bars[split.train.start:split.train.stop]
        test_bars = bars[split.test.start:split.test.stop]

        best_key = (False, _NEG_INF)
        best_params: dict[str, int] | None = None
        best_config: BacktestConfig | None = None
        best_is_metrics: BacktestMetrics | None = None
        for point in grid.points():
            overrides = dict(zip(axis_names, point, strict=True))
            config = base_config.model_copy(update=overrides)
            is_result = run_backtest(
                config, fsm_config, train_bars, indicator_service=indicator_service
            )
            sharpe = is_result.metrics.sharpe_ratio
            key = (sharpe is not None, sharpe if sharpe is not None else _NEG_INF)
            if key > best_key:
                best_key, best_params, best_config = key, overrides, config
                best_is_metrics = is_result.metrics
        if best_params is None or best_config is None or best_is_metrics is None:
            raise WalkForwardError("모든 격자점의 IS Sharpe가 계산 불가(None)다")

        oos_result = run_backtest(
            best_config, fsm_config, test_bars, indicator_service=indicator_service
        )
        windows.append(WindowOutcome(
            split=split, selected_params=best_params,
            is_metrics=best_is_metrics, oos_metrics=oos_result.metrics,
        ))
        oos_curves.append(oos_result.equity_curve)
        oos_fills.extend(oos_result.fills)

    stitched = _stitch(oos_curves, base_config.initial_equity)
    oos_stitched_metrics = compute_metrics(
        equity_curve=stitched, fills=oos_fills,
        initial_equity=base_config.initial_equity, periods_per_year=base_config.periods_per_year,
    )
    return WalkForwardReport(windows=tuple(windows), oos_stitched_metrics=oos_stitched_metrics)


def _stitch(curves: list[list[EquityPoint]], initial_equity: Decimal) -> list[EquityPoint]:
    """Chains each window's OOS equity curve onto the previous one's ending equity
    (each window replays its own test slice starting fresh from `initial_equity`, so
    the raw values aren't comparable across windows without rebasing), then
    recomputes drawdown against a single running peak over the whole stitched
    series -- per-window drawdown alone would understate the true OOS max drawdown."""
    stitched: list[EquityPoint] = []
    running_equity = initial_equity
    next_bar_index = 0
    for curve in curves:
        start_equity = curve[0].equity
        scale = Decimal("1") if start_equity == 0 else running_equity / start_equity
        for point in curve:
            stitched.append(EquityPoint(
                bar_index=next_bar_index, timestamp=point.timestamp,
                equity=point.equity * scale, drawdown_pct=Decimal("0"),
            ))
            next_bar_index += 1
        running_equity = curve[-1].equity * scale
    peak = stitched[0].equity
    for i, point in enumerate(stitched):
        peak = max(peak, point.equity)
        drawdown = Decimal("0") if peak <= 0 else (peak - point.equity) / peak * 100
        stitched[i] = EquityPoint(
            bar_index=point.bar_index, timestamp=point.timestamp,
            equity=point.equity, drawdown_pct=drawdown,
        )
    return stitched
