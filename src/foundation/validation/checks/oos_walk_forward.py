"""L39 -- checks/oos_walk_forward.py: check 3, out-of-sample walk-forward.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md #9 L39 / #3.5-A row
410 (hard-fail table) / row 415 (soft FAIL rule). Builds `ctx.policy`-shaped
walk-forward splits (`splits.make_splits`), re-validates them
(`assert_no_overlap` -- defense in depth on top of `run_walk_forward`'s own
internal re-check), then runs `run_walk_forward` and reads the OOS-stitched
net Sharpe off the result.

hard fail (`VALIDATION_OOS_LEAKAGE` / `VALIDATION_OOS_INSUFFICIENT`) is
reserved for split-construction failures the policy's own thresholds
produced -- not enough bars for `policy.min_oos_windows` windows, a
purge/embargo gap `splits.py` itself refuses to honor, or every grid
candidate coming back with an unscoreable (`None`) in-sample Sharpe
(`WalkForwardError` -- there is no OOS basis to fall back on, so this
collapses into "insufficient OOS evidence" rather than a distinct code).
A soft FAIL (not hard) is `oos net Sharpe <= 0` per #3.5-A row 415 -- a
strategy that loses money out-of-sample is not a data-integrity problem,
so it downgrades a validation run rather than aborting it.
"""

from __future__ import annotations

from typing import Any, Literal

from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.backtest.application.walk_forward import (
    WalkForwardError,
    run_walk_forward,
)
from src.foundation.backtest.domain.param_stability import ParamGrid
from src.foundation.backtest.domain.splits import (
    MinTrainUnsatisfiableError,
    OosLeakageError,
    assert_no_overlap,
    make_splits,
)
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

__all__ = ["CHECK_TYPE", "run"]

CHECK_TYPE = "oos_walk_forward"

# `sweep`/`run_walk_forward` (L35) restrict grid axes to `BacktestConfig`'s
# own int fields (both reject any axis name not in `BacktestConfig.
# model_fields`) -- `warmup_bars` is the only field that fits that contract
# and is meaningful to vary, so it is this leaf's v1 sweep axis.
# `CheckContext`'s field set is spec-locked (test_context.py's
# `test_field_order_matches_spec_signature`, from sibling leaf L38), so
# there is no ctx field to source a caller-supplied grid from instead.
_GRID = ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})

# Not pinned by `ValidationPolicy` (only `min_oos_windows`/`purge_bars`/
# `embargo_bars`/`min_grid_points` are) -- each split's train-window floor
# is set as a fraction of the full bar series so it scales with however
# much data the caller actually provides.
_MIN_TRAIN_FRACTION = 0.2


def run(ctx: CheckContext) -> CheckResult:
    policy = ctx.policy
    bars = list(ctx.bars.upto(len(ctx.bars) - 1))
    fsm_config = FSMStrategyConfig.model_validate(ctx.artifact.fsm_definition)
    n_bars = len(bars)
    min_train_bars = max(1, int(n_bars * _MIN_TRAIN_FRACTION))
    walk_forward_mode: Literal["anchored", "rolling"] = (
        "rolling" if policy.oos_mode == "ROLLING" else "anchored"
    )

    try:
        splits = make_splits(
            n_bars=n_bars,
            n_splits=policy.min_oos_windows,
            mode=walk_forward_mode,
            purge=policy.purge_bars,
            embargo=policy.embargo_bars,
            min_train=min_train_bars,
        )
        assert_no_overlap(splits)
    except MinTrainUnsatisfiableError as exc:
        return _hard_fail(ctx, "VALIDATION_OOS_INSUFFICIENT", str(exc))
    except OosLeakageError as exc:
        return _hard_fail(ctx, "VALIDATION_OOS_LEAKAGE", str(exc))

    if len(splits) < policy.min_oos_windows:
        return _hard_fail(
            ctx,
            "VALIDATION_OOS_INSUFFICIENT",
            f"produced {len(splits)} windows, need >= {policy.min_oos_windows}",
        )

    try:
        report = run_walk_forward(ctx.config, fsm_config, bars, _GRID, splits)
    except (WalkForwardError, BacktestRunError) as exc:
        return _hard_fail(ctx, "VALIDATION_OOS_INSUFFICIENT", str(exc))

    oos_metrics = report.oos_stitched_metrics
    oos_sharpe = oos_metrics.sharpe_ratio
    outcome = Outcome.PASS if oos_sharpe is not None and oos_sharpe > 0 else Outcome.FAIL

    metrics: dict[str, Any] = {
        "period_start": oos_metrics.period_start,
        "period_end": oos_metrics.period_end,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
        "oos_windows_count": len(splits),
        "selection_rule": report.selection_rule,
        "oos_net_sharpe": oos_sharpe,
        "oos_net_return_pct": oos_metrics.net_return_pct,
        "oos_max_drawdown_pct": oos_metrics.max_drawdown_pct,
    }
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=outcome,
        metrics=metrics,
        result_hash=compute_result_hash(metrics),
        policy_version=policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )


def _hard_fail(ctx: CheckContext, code: str, reason: str) -> CheckResult:
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
        hard_fail_reasons=[code],
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
