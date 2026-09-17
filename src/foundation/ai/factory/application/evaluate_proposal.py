"""EvaluateProposal command -- AI-12.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.3 AI-12
`application/evaluate_proposal.py` ("validation pipeline (L36-L44) call ->
outcome recorded. Stop on FAIL"), §9 AI-12 DoD ("threshold not met -> FAIL
recorded (I-07)"), depends on AI-9 (`generate_proposal.py`, an already
-accepted `StrategyProposal`) and L42 (`domain/rules.py::evaluate_bundle`,
`L4_strategy_portfolio_backtest_v1.0.md` §9 L42).

This module delegates the PASS/FAIL *decision* to `evaluate_bundle` alone --
it never re-derives "which warnings count as a hard fail" itself, matching
`application/generate_proposal.py`'s own discipline of never reimplementing
`domain/proposal_rules.py`'s checks. What it does *not* do is run the checks
themselves: `evaluate_bundle` consumes `CheckResult` (`domain/check_result.py`),
and every existing `CheckResult` producer (`src/foundation/validation/checks/
{context,point_in_time,backtest,oos_walk_forward,robustness,stress_capacity,
failure_conditions}.py`) is wired to `CheckContext`, which in turn requires a
`StrategyArtifact.fsm_definition` (`domain/artifact.py`) -- the FSM-based
strategy representation `src/services/strategy_builder_service.py` produces
for a human-authored strategy. An AI proposal's `script_source` is an AIOS
Script (DSL-1..12) compiled to `CompiledScript`/IR, not an FSM definition, and
no leaf in either L4 spec bridges the two (confirmed: no file under `src/`
imports both a script-compiler symbol and an FSM/validation symbol together).
Building that bridge is out of this leaf's 240-line budget and not named by
any leaf in `L4_ai_research_strategy_factory_v1.0.md` or
`L4_strategy_portfolio_backtest_v1.0.md` -- so, consistent with CLAUDE.md's
"unverified external facts raise `NotImplementedError`" ratchet (the same
posture applied here to an unbuilt internal bridge, not an external fact),
this module accepts an already-computed `check_results: Sequence[CheckResult]`
from its caller rather than running checks itself. A future leaf that
compiles+backtests the proposal's script (most likely via the BT-10/BT-10b
`compile_source -> build_script_signal_source -> run_quick_backtest` chain
`src/api/routers/backtests.py` already wires end to end) and shapes the
result into `CheckResult` rows is the intended supplier of that argument;
this leaf's job starts at "checks already ran" and ends at "outcome decided
and recorded" -- exactly the AI-12 row's own two clauses.

`record_experiment` (AI-11) is the sole persistence path -- both PASS and
FAIL are recorded (spec DoD "FAIL 기록", not "FAIL discarded"); "FAIL이면
종료" is satisfied by this function never attempting anything past recording
(there is no promotion call here for either outcome -- that is AI-13's
`promote_to_paper`, a separate leaf this module does not import), and by the
returned `ProposalEvaluation.accepted` flag being the only thing a caller
needs to gate on before ever considering promotion.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from src.foundation.ai.factory.contracts.v1 import ProposalEvaluation, StrategyProposal
from src.foundation.experiments.application.record import record_experiment
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.experiments.ports.repository import ExperimentRepository
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome as ValidationOutcome
from src.foundation.validation.domain.rules import evaluate_bundle

__all__ = [
    "EvaluateProposalError",
    "NoCheckResultsError",
    "DuplicateCheckTypeError",
    "evaluate_proposal",
]


class EvaluateProposalError(Exception):
    """Common base for this module's own (non-`evaluate_bundle`) rejections."""


class NoCheckResultsError(EvaluateProposalError):
    """`evaluate_bundle([])` returns `PASS` by construction (no hard-fail
    reasons to union) -- this module refuses to let "no check ran at all"
    silently read as "passed every check" (I-07 "a proposal must pass a
    deterministic gate", not pass by omission)."""

    def __init__(self) -> None:
        super().__init__("evaluate_proposal: check_results must not be empty (fail-closed)")


class DuplicateCheckTypeError(EvaluateProposalError):
    """Two `CheckResult`s sharing one `check_type` would silently overwrite
    each other in the recorded metrics -- the second copy's metrics would
    replace the first's in `Experiment.metrics` even though both already
    contributed their `hard_fail_reasons`/`obligations` to `evaluate_bundle`'s
    union, leaving the recorded evidence inconsistent with the decision it
    documents."""

    def __init__(self, check_type: str) -> None:
        self.check_type = check_type
        super().__init__(f"evaluate_proposal: duplicate check_type {check_type!r}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _merge_check_metrics(results: Sequence[CheckResult]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for result in results:
        if result.check_type in merged:
            raise DuplicateCheckTypeError(result.check_type)
        merged[result.check_type] = {
            "outcome": result.outcome.value,
            "metrics": result.metrics,
            "warnings": list(result.warnings),
            "hard_fail_reasons": list(result.hard_fail_reasons),
            "obligations": list(result.obligations),
        }
    return merged


async def evaluate_proposal(
    *,
    proposal: StrategyProposal,
    check_results: Sequence[CheckResult],
    inputs_hash: str,
    reproducibility_key: str,
    repository: ExperimentRepository,
    tenant_id: UUID,
    created_by: UUID,
    parent_id: UUID | None = None,
    experiment_id_factory: Callable[[], UUID] = uuid4,
    clock: Callable[[], datetime] = _utcnow,
) -> ProposalEvaluation:
    """§2.3 AI-12 row verbatim: validation pipeline call -> outcome recorded.

    `check_results` must be non-empty (see `NoCheckResultsError`) and cover
    distinct `check_type`s (see `DuplicateCheckTypeError`) -- both checked
    before `evaluate_bundle` runs, so a malformed input can never be
    laundered into a false `PASS`. The decision itself is entirely
    `evaluate_bundle`'s (L42): this function only maps its 3-tuple result
    onto `Experiment`/`ProposalEvaluation` and appends to the ledger.
    """
    if not check_results:
        raise NoCheckResultsError()

    metrics = _merge_check_metrics(check_results)
    outcome, obligations, hard_fail_reasons = evaluate_bundle(check_results)

    experiment = Experiment(
        experiment_id=experiment_id_factory(),
        tenant_id=tenant_id,
        reproducibility_key=reproducibility_key,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=inputs_hash,
        metrics=metrics,
        parent_id=parent_id,
        created_by=created_by,
        created_at=clock(),
    )
    recorded = await record_experiment(repository, experiment)

    return ProposalEvaluation(
        proposal_id=proposal.proposal_id,
        experiment_id=recorded.experiment_id,
        accepted=outcome != ValidationOutcome.FAIL,
        hard_fail_reasons=tuple(hard_fail_reasons),
        obligations=tuple(obligations),
        metrics=metrics,
    )
