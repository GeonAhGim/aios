"""L40 -- checks/failure_conditions.py: check 6, operational invalidation
criteria (pause/revalidate obligations).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 row 164 / §9 L40,
hard-fail mapping §3.5-A row 6 (failure_conditions): the criteria cannot be
derived without an OOS result, so a missing (or unusable) `oos_walk_forward`
prior result hard-fails as `VALIDATION_NO_INVALIDATION_CRITERIA` instead of
silently emitting no obligations.

This check has no engine dependency of its own (§2 row 164 lists "dependency:
none") -- it only reads `ctx.prior_results["oos_walk_forward"]`, the `CheckResult`
L39's check produces (`context.py`'s documented purpose for `prior_results`:
"lets a later check (e.g. failure_conditions, L40) read an earlier check's
CheckResult ... without re-running it"). L39 does not exist yet at the time
this module is written (task-3361 ran ahead of task-3360's `checks/
oos_walk_forward.py`), so `_OOS_MDD_KEY` below is this module's own
assumption about L39's future output, following §3.5-A's `{name}_{unit}`
metrics-key convention. If L39 lands with a different key, this check must
be updated to match -- an unverified downstream key name, not a guessed
computation (same posture as `NotImplementedError`/ratchet-allow cases,
except the failure mode here already has a real hard-fail path: an
unrecognized/missing key degrades to `VALIDATION_NO_INVALIDATION_CRITERIA`
rather than crashing or fabricating a threshold).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import compute_result_hash

CHECK_TYPE = "failure_conditions"
_OOS_CHECK_TYPE = "oos_walk_forward"
_OOS_MDD_KEY = "oos_max_drawdown_pct"
_MDD_PAUSE_MULTIPLIER = Decimal("1.5")
_REVALIDATE_OBLIGATION = "REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0"

__all__ = ["CHECK_TYPE", "run"]


def _base_metrics(ctx: CheckContext) -> dict[str, Any]:
    return {
        "period_start": ctx.snapshot_ref.from_time,
        "period_end": ctx.snapshot_ref.to_time,
        "periods_per_year": ctx.config.periods_per_year,
        "basis": "PAPER_SIM",
        "config_hash": ctx.config.config_hash(),
    }


def _hard_fail_result(ctx: CheckContext) -> CheckResult:
    metrics = _base_metrics(ctx)
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=Outcome.FAIL,
        metrics=metrics,
        hard_fail_reasons=["VALIDATION_NO_INVALIDATION_CRITERIA"],
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )


def run(ctx: CheckContext) -> CheckResult:
    oos_result = ctx.prior_results.get(_OOS_CHECK_TYPE)
    if oos_result is None:
        return _hard_fail_result(ctx)

    oos_mdd_raw = oos_result.metrics.get(_OOS_MDD_KEY)
    if oos_mdd_raw is None:
        return _hard_fail_result(ctx)
    oos_mdd_pct = Decimal(str(oos_mdd_raw))

    pause_threshold_pct = oos_mdd_pct * _MDD_PAUSE_MULTIPLIER
    obligations = [f"PAUSE_IF_MDD_GT_{pause_threshold_pct}", _REVALIDATE_OBLIGATION]

    metrics = _base_metrics(ctx)
    metrics.update(
        {
            "oos_max_drawdown_pct": oos_mdd_pct,
            "pause_mdd_threshold_pct": pause_threshold_pct,
        }
    )
    return CheckResult(
        check_type=CHECK_TYPE,
        outcome=Outcome.PASS_WITH_OBLIGATIONS,
        metrics=metrics,
        obligations=obligations,
        result_hash=compute_result_hash(metrics),
        policy_version=ctx.policy.policy_version,
        evidence_refs=[f"snapshot:{ctx.snapshot_ref.snapshot_hash}"],
    )
