"""L35 -- `backtest/application/param_sweep.py`: exhaustive deterministic grid sweep.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L35 (contract: #2 table
`param_sweep.py` row). Runs `run_backtest` once per grid point, in the grid's own
deterministic point order (`ParamGrid.points()`), overriding `base_config` fields
named by each axis with that point's value (`BacktestConfig.model_copy(update=...)`)
-- config-level int fields (e.g. `warmup_bars`) are what `ParamGrid`'s
`dict[str, list[int]]` axes can express without inventing a strategy-specific
override language.

`perf_matrix` feeds a later `overfitting.pbo_cscv` (separate leaf, not yet built):
rows are grid points in `points` order, columns are per-block total-return-pct over
`n_blocks` equal contiguous slices of each point's equity curve.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, BacktestMetrics, EquityPoint
from src.foundation.backtest.domain.param_stability import ParamGrid, Point

__all__ = ["SweepError", "SweepPoint", "SweepResult", "sweep"]


class SweepError(ValueError):
    """Fail-closed rejection -- a grid axis name that isn't a `BacktestConfig` field,
    or too few equity points to cut into `n_blocks` blocks."""


@dataclass(frozen=True, slots=True)
class SweepPoint:
    point: Point
    overrides: dict[str, int]
    metrics: BacktestMetrics


@dataclass(frozen=True, slots=True)
class SweepResult:
    points: tuple[SweepPoint, ...]
    perf_matrix: tuple[tuple[Decimal, ...], ...]


def sweep(
    base_config: BacktestConfig,
    fsm_config: FSMStrategyConfig,
    bars: list[Candle],
    grid: ParamGrid,
    *,
    n_blocks: int = 4,
    indicator_service: IndicatorService | None = None,
) -> SweepResult:
    if n_blocks < 1:
        raise SweepError(f"n_blocks는 1 이상이어야 한다: {n_blocks}")
    axis_names = list(grid.axes.keys())
    unknown = [name for name in axis_names if name not in BacktestConfig.model_fields]
    if unknown:
        raise SweepError(f"grid axes가 BacktestConfig 필드에 없다: {unknown}")

    points: list[SweepPoint] = []
    perf_rows: list[tuple[Decimal, ...]] = []
    for point in grid.points():
        overrides = dict(zip(axis_names, point, strict=True))
        config = base_config.model_copy(update=overrides)
        result = run_backtest(config, fsm_config, bars, indicator_service=indicator_service)
        points.append(SweepPoint(point=point, overrides=overrides, metrics=result.metrics))
        perf_rows.append(tuple(_block_returns(result.equity_curve, n_blocks)))
    return SweepResult(points=tuple(points), perf_matrix=tuple(perf_rows))


def _block_returns(equity_curve: list[EquityPoint], n_blocks: int) -> list[Decimal]:
    n = len(equity_curve)
    if n < n_blocks + 1:
        raise SweepError(f"equity_curve 길이({n})가 n_blocks({n_blocks})를 감당 못한다")
    size, remainder = divmod(n - 1, n_blocks)
    returns: list[Decimal] = []
    start = 0
    for i in range(n_blocks):
        this_size = size + (remainder if i == n_blocks - 1 else 0)
        end = start + this_size
        begin_equity = equity_curve[start].equity
        end_equity = equity_curve[end].equity
        returns.append(
            Decimal("0") if begin_equity <= 0 else (end_equity - begin_equity) / begin_equity * 100
        )
        start = end
    return returns
