"""L38 -- checks/backtest.py: check 2, the primary paper-sim backtest replay.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 row 160 / §9 L38,
hard-fail mapping §3.5-A row 2 (backtest).

`run_backtest` (L31) already replays the strategy and computes cost-aware
metrics; this check adds the two gates §3.5-A assigns to it that
`run_backtest` itself does not enforce: rejecting a cost-free replay when
the policy forbids it (`require_cost_model`, checked *before* replay so a
rejected run never even simulates fills -- `VALIDATION_COST_MODEL_REQUIRED`)
and turning a look-ahead violation the replay path may raise
(`LookaheadViolationError`, I2) into a hard-fail result instead of letting it
escape as an uncaught exception (`BACKTEST_LOOKAHEAD_VIOLATION`) -- plus a
buy&hold benchmark return alongside the strategy's own net return (spec row
160: benchmark reported side by side with the strategy result).

`run_backtest`'s own `BacktestRunError` (e.g. insufficient warmup) is
deliberately *not* caught here -- per `validation.domain.rules`'s docstring,
that is a replay-execution failure that predates policy judgement, and the
application layer (L41 `run_check.py`) turns it into a FAILED run with an
ERROR-outcome evidence row, not a `CheckResult`.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.rules import (
    CostModelRequiredError,
    LookaheadViolationError,
    require_cost_model,
)
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

CHECK_TYPE = "backtest"

__all__ = ["CHECK_TYPE", "run"]


def _benchmark_buy_hold_return_pct(bars: Sequence[Candle]) -> Decimal | None:
    if len(bars) < 2 or bars[0].close == 0:
        return None
    return (bars[-1].close - bars[0].close) / bars[0].close * Decimal("100")


def _base_metrics(ctx: CheckContext, *, period_start: Any, period_end: Any) -> dict[str, Any]:
    return {
        "period_start": period_start,
        "period_end": period_end,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
    }


def _hard_fail_result(ctx: CheckContext, *, reason: str, metrics: dict[str, Any]) -> CheckResult:
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=Outcome.FAIL,
        metrics=metrics,
        hard_fail_reasons=[reason],
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )


def run(ctx: CheckContext) -> CheckResult:
    try:
        require_cost_model(ctx.config.cost_model, allow_zero=ctx.policy.allow_zero_cost)
    except CostModelRequiredError:
        metrics = _base_metrics(
            ctx, period_start=ctx.snapshot_ref.from_time, period_end=ctx.snapshot_ref.to_time
        )
        return _hard_fail_result(ctx, reason="VALIDATION_COST_MODEL_REQUIRED", metrics=metrics)

    bars = list(ctx.bars.upto(len(ctx.bars) - 1))
    fsm_config = FSMStrategyConfig.model_validate(ctx.artifact.fsm_definition)

    try:
        result = run_backtest(ctx.config, fsm_config, bars)
    except LookaheadViolationError:
        metrics = _base_metrics(ctx, period_start=bars[0].open_time, period_end=bars[-1].close_time)
        return _hard_fail_result(ctx, reason="BACKTEST_LOOKAHEAD_VIOLATION", metrics=metrics)

    metrics = _base_metrics(
        ctx, period_start=result.metrics.period_start, period_end=result.metrics.period_end
    )
    metrics.update(
        {
            "net_return_pct": result.metrics.net_return_pct,
            "gross_return_pct": result.metrics.gross_return_pct,
            "total_return_pct": result.metrics.total_return_pct,
            "max_drawdown_pct": result.metrics.max_drawdown_pct,
            "sharpe_ratio": result.metrics.sharpe_ratio,
            "total_trades": result.metrics.total_trades,
            "benchmark_buy_hold_return_pct": _benchmark_buy_hold_return_pct(bars),
        }
    )
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=Outcome.PASS,
        metrics=metrics,
        warnings=list(result.warnings),
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
