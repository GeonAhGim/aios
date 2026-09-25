"""L39 -- checks/robustness.py: check 4, parameter stability + DSR/PBO.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L39 / #3.5
(DSR/PBO formulas) / #3.5-A row 411 (hard-fail table) / row 415 (soft FAIL
rule). Runs one exhaustive grid `sweep` with `_PBO_BLOCKS = 8` block cuts
(spec #3.5's own default block count, S=8, worked out to C(8,4)=70
combinations), matching `overfitting.pbo_cscv`'s own default block count
so `sweep`'s per-point per-block return matrix can feed `pbo_cscv` directly
(transposed to block-rows x grid-point-columns) with each of the 8
already-aggregated block returns as one CSCV block (a degenerate
1-row-per-block `_split_blocks` call inside `pbo_cscv`) -- `C(8, 4) = 70`
combinations, exactly the spec's own worked number.

Then:
- `param_stability.stability_score` for the best point's neighbor
  isolation (spec #3.5's isolation ratio = (best - neighbor_mean)/|best|,
  capped by `max_param_isolation` -- computed here from the report
  directly rather than the domain module's own fixed 0.5 threshold, so
  the FAIL decision tracks `ctx.policy.max_param_isolation`).
- `overfitting.pbo_cscv` on the transposed block-return matrix for PBO.
- `overfitting.deflated_sharpe` for DSR, using the grid's own Sharpe
  distribution for `sr_var` (spec #3.5's own "variance of the grid's
  Sharpe estimates" definition) and the best point's 8 block returns as
  the return sample for skew/kurtosis -- `sweep()` exposes per-block
  returns, not the raw per-bar equity curve, so the block-return sample
  is what this leaf actually has without re-running `run_backtest` a
  second time outside `sweep`'s own loop.

hard fail (`VALIDATION_NONREPRODUCIBLE_CONFIG`) covers the three
conditions #3.5-A row 411 lists: grid smaller than `policy.min_grid_points`,
seed not pinned between `ctx.config.seed` and `ctx.seed`, or the artifact's
`registry_version` drifting from the currently-installed indicator
registry. A soft FAIL (not hard) is `pbo > max_pbo`, `dsr < min_dsr`, or
isolation ratio `> max_param_isolation` per spec row 415.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.indicators.registry import DEFAULT_REGISTRY
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.api import (
    OverfittingError,
    ParamGrid,
    ParamStabilityError,
    Point,
    deflated_sharpe,
    pbo_cscv,
    stability_score,
)
from src.foundation.backtest.application.param_sweep import sweep
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

__all__ = ["CHECK_TYPE", "run"]

CHECK_TYPE = "robustness"
OVERFITTING_VERSION = "ofit-v1"
_HARD_FAIL_CODE = "VALIDATION_NONREPRODUCIBLE_CONFIG"
_PBO_BLOCKS = 8

# Same rationale as `oos_walk_forward.py`'s `_GRID`: `sweep` restricts grid
# axes to `BacktestConfig` int fields, and `CheckContext`'s field set is
# spec-locked by sibling leaf L38's `test_context.py`, so `warmup_bars` is
# this leaf's v1 sweep axis, hardcoded here rather than ctx-supplied.
_GRID = ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})


def run(ctx: CheckContext) -> CheckResult:
    policy = ctx.policy

    if _GRID.size < policy.min_grid_points:
        return _hard_fail(
            ctx, f"grid size {_GRID.size} < policy.min_grid_points {policy.min_grid_points}"
        )
    if ctx.config.seed != ctx.seed:
        return _hard_fail(
            ctx, f"seed not pinned: config.seed={ctx.config.seed} != ctx.seed={ctx.seed}"
        )
    registry_version = DEFAULT_REGISTRY.registry_hash()
    if ctx.artifact.registry_version != registry_version:
        return _hard_fail(
            ctx,
            f"registry version drift: artifact={ctx.artifact.registry_version} "
            f"current={registry_version}",
        )

    bars = list(ctx.bars.upto(len(ctx.bars) - 1))
    fsm_config = FSMStrategyConfig.model_validate(ctx.artifact.fsm_definition)

    n_blocks = min(_PBO_BLOCKS, len(bars) - 1)
    if n_blocks % 2 != 0:
        n_blocks -= 1
    if n_blocks < 2:
        return _hard_fail(ctx, f"not enough bars ({len(bars)}) for CSCV blocking")

    result = sweep(ctx.config, fsm_config, bars, _GRID, n_blocks=n_blocks)
    metric_by_point: dict[Point, Decimal] = {}
    for point in result.points:
        sharpe = point.metrics.sharpe_ratio
        if sharpe is None:
            return _hard_fail(ctx, "grid contains an unscoreable (None) Sharpe point")
        metric_by_point[point.point] = sharpe

    try:
        stability = stability_score(_GRID, metric_by_point)
    except ParamStabilityError as exc:
        return _hard_fail(ctx, str(exc))

    best_value = metric_by_point[stability.best]
    isolation_ratio = (
        None if best_value == 0 else (best_value - stability.neighbor_mean) / abs(best_value)
    )
    best_index = next(i for i, p in enumerate(result.points) if p.point == stability.best)

    perf_matrix_for_pbo = [list(row) for row in zip(*result.perf_matrix, strict=True)]
    warnings: list[str] = []
    pbo: Decimal | None
    try:
        pbo = pbo_cscv(perf_matrix_for_pbo, n_blocks=n_blocks)
    except OverfittingError as exc:
        pbo = None
        warnings.append(f"pbo_uncomputable: {exc}")

    dsr = _try_deflated_sharpe(
        sr_hat=best_value,
        n_trials=_GRID.size,
        T=len(bars) - 1,
        sharpe_values=list(metric_by_point.values()),
        block_returns=result.perf_matrix[best_index],
        warnings=warnings,
    )

    fail_reasons: list[str] = []
    if pbo is not None and pbo > policy.max_pbo:
        fail_reasons.append("pbo_over_max")
    if dsr is not None and dsr < policy.min_dsr:
        fail_reasons.append("dsr_under_min")
    if isolation_ratio is not None and isolation_ratio > policy.max_param_isolation:
        fail_reasons.append("param_isolated")
    outcome = Outcome.FAIL if fail_reasons else Outcome.PASS

    metrics: dict[str, Any] = {
        "period_start": ctx.snapshot_ref.from_time,
        "period_end": ctx.snapshot_ref.to_time,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
        "grid_size": _GRID.size,
        "best_point": list(stability.best),
        "best_sharpe": best_value,
        "neighbor_mean_sharpe": stability.neighbor_mean,
        "isolated": stability.isolated,
        "isolation_ratio": isolation_ratio,
        "pbo": pbo,
        "dsr": dsr,
        "fail_reasons": fail_reasons,
    }
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=outcome,
        metrics=metrics,
        warnings=warnings,
        result_hash=compute_result_hash(metrics),
        policy_version=policy.policy_version,
        overfitting_version=OVERFITTING_VERSION,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )


def _try_deflated_sharpe(
    *,
    sr_hat: Decimal,
    n_trials: int,
    T: int,
    sharpe_values: list[Decimal],
    block_returns: tuple[Decimal, ...],
    warnings: list[str],
) -> Decimal | None:
    if len(sharpe_values) < 2:
        warnings.append("dsr_uncomputable: fewer than 2 grid Sharpe values")
        return None
    sr_mean = sum(sharpe_values, Decimal(0)) / len(sharpe_values)
    sr_var = sum(((v - sr_mean) ** 2 for v in sharpe_values), Decimal(0)) / len(sharpe_values)
    if sr_var <= 0:
        warnings.append("dsr_uncomputable: zero-variance grid Sharpe distribution")
        return None
    if len(block_returns) < 2:
        warnings.append("dsr_uncomputable: fewer than 2 block returns for skew/kurtosis")
        return None
    ret_mean = sum(block_returns, Decimal(0)) / len(block_returns)
    ret_var = sum(((v - ret_mean) ** 2 for v in block_returns), Decimal(0)) / len(block_returns)
    ret_std = ret_var.sqrt()
    if ret_std == 0:
        warnings.append("dsr_uncomputable: zero-variance block returns")
        return None
    skew = (sum(((v - ret_mean) ** 3 for v in block_returns), Decimal(0)) / len(block_returns)) / (
        ret_std**3
    )
    kurt = (sum(((v - ret_mean) ** 4 for v in block_returns), Decimal(0)) / len(block_returns)) / (
        ret_std**4
    )
    try:
        return deflated_sharpe(sr_hat, n_trials, T, skew, kurt, sr_var)
    except (OverfittingError, ValueError) as exc:
        warnings.append(f"dsr_uncomputable: {exc}")
        return None


def _hard_fail(ctx: CheckContext, reason: str) -> CheckResult:
    metrics: dict[str, Any] = {
        "period_start": ctx.snapshot_ref.from_time,
        "period_end": ctx.snapshot_ref.to_time,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
        "reason": reason,
    }
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=Outcome.FAIL,
        metrics=metrics,
        hard_fail_reasons=[_HARD_FAIL_CODE],
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
