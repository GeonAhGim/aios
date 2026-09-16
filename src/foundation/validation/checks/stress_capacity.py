"""L40 -- checks/stress_capacity.py: check 5, stress-scenario resilience and
capacity.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 row 163 / §9 L40,
hard-fail mapping §3.5-A row 5 (stress_capacity): any of the policy's
`required_stress` scenarios missing a result -> `VALIDATION_SCENARIO_MISSING`.

Reuses `run_stress` (L35) for MDD/turnover per scenario -- I-05 never
reimplements fill/backtest arithmetic. `run_stress`'s own `missing` list is
computed against a hardcoded module constant (`stress.REQUIRED_SCENARIOS`);
this check ignores that and recomputes `missing` against
`ctx.policy.required_stress` instead, since the policy is the actual
authority a check judges against -- a policy edit that trims/extends the
required set must be honoured here without touching `stress.py`.

`BacktestMetrics` does not carry raw per-fill notional (only the aggregate
`turnover`), so "capacity" (spec row 163: average filled notional divided by
average bar quote volume) is derived from `turnover` and `total_trades`
rather than re-running the simulation a second time just to collect fills:
avg_fill_notional ~=
turnover * initial_equity / (total_trades * 2) -- one entry fill and one
exit fill per closed round trip, the same assumption
`compute_metrics._round_trips` already makes for this phase. Documented
simplification; `None` when there are no closed trades (division undefined,
not a silent zero).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.stress import run_stress
from src.foundation.backtest.domain.models import BacktestMetrics
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

CHECK_TYPE = "stress_capacity"
_FILLS_PER_ROUND_TRIP = 2

__all__ = ["CHECK_TYPE", "run"]


def _avg_bar_quote_volume(bars: list[Candle]) -> Decimal | None:
    if not bars:
        return None
    total = sum((bar.close * bar.volume for bar in bars), Decimal("0"))
    return total / len(bars)


def _capacity_ratio(
    metrics: BacktestMetrics,
    *,
    initial_equity: Decimal,
    avg_bar_quote_volume: Decimal | None,
) -> Decimal | None:
    if metrics.total_trades <= 0 or avg_bar_quote_volume is None or avg_bar_quote_volume <= 0:
        return None
    avg_fill_notional = (
        metrics.turnover * initial_equity / (metrics.total_trades * _FILLS_PER_ROUND_TRIP)
    )
    return avg_fill_notional / avg_bar_quote_volume


def run(ctx: CheckContext) -> CheckResult:
    bars = list(ctx.bars.upto(len(ctx.bars) - 1))
    fsm_config = FSMStrategyConfig.model_validate(ctx.artifact.fsm_definition)
    avg_bar_quote_volume = _avg_bar_quote_volume(bars)

    report = run_stress(ctx.config, fsm_config, bars, list(ctx.policy.required_stress))
    missing = [name for name in ctx.policy.required_stress if name not in report.per_scenario]

    scenarios: dict[str, dict[str, Any]] = {
        name: {
            "max_drawdown_pct": scenario_metrics.max_drawdown_pct,
            "turnover": scenario_metrics.turnover,
            "capacity_ratio": _capacity_ratio(
                scenario_metrics,
                initial_equity=ctx.config.initial_equity,
                avg_bar_quote_volume=avg_bar_quote_volume,
            ),
        }
        for name, scenario_metrics in report.per_scenario.items()
    }

    metrics: dict[str, Any] = {
        "period_start": bars[0].open_time if bars else ctx.snapshot_ref.from_time,
        "period_end": bars[-1].close_time if bars else ctx.snapshot_ref.to_time,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
        "scenarios": scenarios,
        "missing_scenarios": missing,
    }
    hard_fail_reasons = ["VALIDATION_SCENARIO_MISSING"] if missing else []
    outcome = Outcome.FAIL if hard_fail_reasons else Outcome.PASS
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=outcome,
        metrics=metrics,
        hard_fail_reasons=hard_fail_reasons,
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
