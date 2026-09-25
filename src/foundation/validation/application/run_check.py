"""L4_strategy_portfolio_backtest_v1.0.md §9 L41 -- application/run_check.py:
runs one of the six required validation checks (`policy.REQUIRED_CHECKS`)
against an already-assembled `CheckContext` (L38), persists the run/result
through the existing generic `ValidationRepository` (its
`complete_with_result` already wraps the RUNNING->SUCCEEDED transition and
the result insert in one DB transaction, unchanged since the pre-L36
"backtest"-only leaf -- this function reuses that transaction, it does not
extend the schema), and appends an audit evidence row via
`record_command_event` (`src.foundation.evidence`, wired here per its own
"host contexts call this once their own commit lands" docstring).

`checks/backtest.py`'s docstring documents the split explicitly: `run_backtest`'s
own `BacktestRunError` (e.g. insufficient warmup) is deliberately *not* caught
inside `checks/backtest.run()` -- it is a replay-execution failure that
predates policy judgement, and this module turns it into a FAILED run with an
ERROR-outcome evidence row instead of a `CheckResult`.

`CheckContext` assembly (fetching bars/universe/snapshot_ref for a strategy)
and sequencing all six checks into one bundle (populating `prior_results`
across calls) are out of this leaf's file scope (§9 L41: only
`compile_artifact.py`/`run_check.py`) -- that orchestration is L43's
`build_bundle.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Outcome as AuditOutcome
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.validation.checks import (
    backtest,
    failure_conditions,
    oos_walk_forward,
    point_in_time,
    robustness,
    stress_capacity,
)
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import ValidationResult
from src.foundation.validation.ports.repository import ValidationRepository

CHECK_RUNNERS: dict[str, Callable[[CheckContext], CheckResult]] = {
    point_in_time.CHECK_TYPE: point_in_time.run,
    backtest.CHECK_TYPE: backtest.run,
    oos_walk_forward.CHECK_TYPE: oos_walk_forward.run,
    robustness.CHECK_TYPE: robustness.run,
    stress_capacity.CHECK_TYPE: stress_capacity.run,
    failure_conditions.CHECK_TYPE: failure_conditions.run,
}
"""Every entry's key is that module's own `CHECK_TYPE` constant, so a
mismatch between this table and a check module's declared type is
impossible by construction (no hand-copied string literal to drift)."""

__all__ = [
    "CHECK_RUNNERS",
    "CheckAlreadyInProgressError",
    "UnknownCheckTypeError",
    "run_check",
]


class UnknownCheckTypeError(Exception):
    """`check_type` is not one of `CHECK_RUNNERS` (`policy.REQUIRED_CHECKS`)."""


class CheckAlreadyInProgressError(Exception):
    """Mirrors `start_validation.py`'s `ValidationAlreadyInProgressError`
    (STR-007) -- the same exact (artifact, policy, snapshot, check_type,
    seed) combination is already running under a different call; the caller
    should retry shortly instead of racing a second run into existence."""


def _input_snapshot_hash(ctx: CheckContext, check_type: str) -> str:
    """This leaf's file scope (§9 L41) does not include extending
    `domain/rules.py`, so the idempotency key `strategy_validation_run.
    input_snapshot_hash` needs for `run_check` is assembled here instead of
    a domain-level hash function. Combining `artifact_hash` (content-addressed
    strategy shape), `policy.policy_hash()`, `snapshot_ref.snapshot_hash`,
    `check_type`, and `seed` means any one of those changing produces a
    different run -- the same reproducibility rule `compute_input_snapshot_hash`
    already applies to the pre-L36 single-check flow, generalized to a
    context that carries its own content hashes instead of raw payloads."""
    payload = {
        "artifact_hash": ctx.artifact.artifact_hash,
        "policy_hash": ctx.policy.policy_hash(),
        "snapshot_hash": ctx.snapshot_ref.snapshot_hash,
        "check_type": check_type,
        "seed": ctx.seed,
    }
    return sha256_hex(canonical_json(payload))


def _cost_model_dict(ctx: CheckContext) -> dict[str, str]:
    return {
        "fee_bps": str(ctx.config.cost_model.fee_bps),
        "slippage_bps": str(ctx.config.cost_model.slippage_bps),
    }


def _json_safe_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """`checks/*.py` build `CheckResult.metrics` as a plain dict that may
    hold `datetime`/`Decimal` values (e.g. `checks/backtest.py`'s
    `period_start`/`period_end`) -- fine for `domain/rules.compute_result_hash`,
    which already hashes them via `json.dumps(..., default=str)`, but the
    JSONB adapter's `complete_with_result` does a bare `json.dumps` with no
    such fallback. `start_validation.py`'s pre-L36 flow never hit this
    because its metrics came from a pydantic model's `model_dump(mode="json")`;
    a raw check-module dict has no such built-in normalization, so this
    leaf's insert boundary does it once, the same way, before persisting."""
    return cast(dict[str, Any], json.loads(json.dumps(metrics, default=str)))


async def run_check(
    validation_repo: ValidationRepository,
    *,
    check_type: str,
    ctx: CheckContext,
    owner_user_id: UUID,
    audit_repo: AuditEventRepository | None = None,
) -> ValidationResult:
    runner = CHECK_RUNNERS.get(check_type)
    if runner is None:
        raise UnknownCheckTypeError(check_type)

    input_snapshot_hash = _input_snapshot_hash(ctx, check_type)
    strategy_id = ctx.artifact.strategy_id
    strategy_version = ctx.artifact.version

    existing_run = await validation_repo.get_run_by_snapshot(
        strategy_id, strategy_version, check_type, input_snapshot_hash
    )
    if existing_run is not None:
        existing_result = await validation_repo.get_result_for_run(existing_run.id)
        if existing_result is not None:
            return existing_result
        raise CheckAlreadyInProgressError(
            f"{strategy_id}/{strategy_version}'s {check_type} check is already "
            "running under another request -- retry shortly."
        )

    try:
        run = await validation_repo.create_run(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            check_type=check_type,
            input_snapshot_hash=input_snapshot_hash,
            cost_model=_cost_model_dict(ctx),
            warmup_bars=ctx.config.warmup_bars,
            periods_per_year=ctx.config.periods_per_year,
            initial_equity=ctx.config.initial_equity,
        )
    except ConcurrencyConflictError as exc:
        winner = await validation_repo.get_run_by_snapshot(
            strategy_id, strategy_version, check_type, input_snapshot_hash
        )
        if winner is None:  # pragma: no cover -- a UNIQUE violation guarantees this exists
            raise
        winner_result = await validation_repo.get_result_for_run(winner.id)
        if winner_result is None:
            raise CheckAlreadyInProgressError(
                f"{strategy_id}/{strategy_version}'s {check_type} check is already "
                "running under another request -- retry shortly."
            ) from exc
        return winner_result

    run = await validation_repo.mark_running(run.id)

    try:
        check_result = runner(ctx)
    except BacktestRunError as exc:
        await validation_repo.mark_failed(run.id)
        if audit_repo is not None:
            await record_command_event(
                audit_repo,
                tenant_id=owner_user_id,
                aggregate_type="strategy_validation_run",
                aggregate_id=run.id,
                action=f"validation_check_errored:{check_type}",
                actor_subject_id=owner_user_id,
                outcome=AuditOutcome.ERROR,
                payload={"check_type": check_type, "reason": str(exc)},
            )
        raise

    result = ValidationResult(
        id=uuid4(),
        run_id=run.id,
        outcome=check_result.outcome,
        metrics=_json_safe_metrics(check_result.metrics),
        warnings=tuple(check_result.warnings),
        hard_fail_reasons=tuple(check_result.hard_fail_reasons),
        obligations=tuple(check_result.obligations),
        result_hash=check_result.result_hash,
        evidence_refs=tuple(check_result.evidence_refs),
    )
    _, saved_result = await validation_repo.complete_with_result(run.id, result)

    if audit_repo is not None:
        await record_command_event(
            audit_repo,
            tenant_id=owner_user_id,
            aggregate_type="strategy_validation_run",
            aggregate_id=run.id,
            action=f"validation_check_completed:{check_type}",
            actor_subject_id=owner_user_id,
            outcome=AuditOutcome.SUCCESS,
            payload={"check_type": check_type, "outcome": check_result.outcome.value},
        )

    return saved_result
