"""L4_risk_and_safety_v1.0.md#§2 line 112, §9 R-54 — recompute and compare
stored decisions.

Spec: prior art R-16 (`src/core/risk/evaluator.py`), R-22
(`postgres_bundle_repository.py`), R-24 (`postgres_decision_repository.py`).
Feed the `inputs_snapshot` stored in the WORM row, together with the
rule_bundle matching the `rule_hash` that decision actually used, straight
through to R-16 `evaluate()` as "pinned" input to get a re-judgment — the
judgment logic (threshold comparisons) is not reimplemented here (§C forbids
duplicating context). A mismatch is observed as a +1 increment of
`aios.core_risk.replay_mismatch.count_total` (§5·§7) and surfaced by the
caller (`src/tools/risk_replay.py`) as an exit code via
`INTEGRITY_RISK_REPLAY_MISMATCH` (§ error taxonomy line 295).

`decision_repo`/`bundle_repo` require only a structural type (Protocol) — the
existing `PostgresDecisionRepository.get()`/
`PostgresBundleRepository.get_by_rule_hash()` already satisfy it as-is (no
separate adapter needed).

task-2395 — `decision_repo.get()` may raise `DecisionCorruptError` (a stored
row that fails the `RiskDecision` contract, e.g. NULL `latency_us`). This
function does not catch it and lets it propagate to the caller
(`src/tools/risk_replay.py`) unchanged — like `BundleNotFoundError`, "no
matching bundle" and "corrupt row" are distinct failure modes, so each keeps
its own exception type instead of being disguised as a diff.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from src.core.observability.metric_names import CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics_registry import get_registry
from src.core.risk.decision import RiskDecision
from src.core.risk.evaluator import evaluate
from src.core.risk.inputs import RiskInputs
from src.core.risk.policy_bundle import RiskRuleBundle

INTEGRITY_RISK_REPLAY_MISMATCH = "INTEGRITY_RISK_REPLAY_MISMATCH"

# Fields to compare between the recomputed result and the stored decision —
# all are the evaluator's "judgment output," not its recomputation input
# (rule_version/rule_hash/engine_version always match because they come from
# the same bundle, and inputs_hash always matches because it comes from the
# same inputs_snapshot).
_COMPARED_FIELDS: tuple[str, ...] = (
    "decision_id",
    "outcome",
    "reason_codes",
    "obligations",
    "rule_results",
)


class DecisionSource(Protocol):
    async def get(self, decision_id: UUID) -> tuple[RiskDecision, dict[str, Any]] | None: ...


class BundleSource(Protocol):
    async def get_by_rule_hash(self, rule_hash: str) -> RiskRuleBundle | None: ...


@dataclass(frozen=True)
class ReplayResult:
    match: bool
    diff: dict[str, Any]


class DecisionNotFoundError(LookupError):
    """`decision_id` is not in the WORM ledger."""


class BundleNotFoundError(LookupError):
    """No rule_bundle matches the stored `rule_hash` — signals bundle deletion/corruption."""


async def replay(
    decision_repo: DecisionSource,
    bundle_repo: BundleSource,
    *,
    decision_id: UUID,
) -> ReplayResult:
    stored = await decision_repo.get(decision_id)
    if stored is None:
        raise DecisionNotFoundError(str(decision_id))
    decision, inputs_snapshot = stored

    bundle = await bundle_repo.get_by_rule_hash(decision.rule_hash)
    if bundle is None:
        raise BundleNotFoundError(decision.rule_hash)

    inputs = RiskInputs.model_validate(inputs_snapshot)
    ttl_seconds = (decision.expires_at - decision.evaluated_at).total_seconds()
    recomputed = evaluate(
        inputs,
        bundle,
        gate_kind=decision.gate_kind,
        trace_id=decision.trace_id,
        now=decision.evaluated_at,
        ttl=ttl_seconds,
    )

    diff: dict[str, Any] = {}
    for field in _COMPARED_FIELDS:
        stored_value = getattr(decision, field)
        recomputed_value = getattr(recomputed, field)
        if stored_value != recomputed_value:
            diff[field] = {"stored": stored_value, "recomputed": recomputed_value}

    match = not diff
    if not match:
        get_registry().counter(CORE_RISK_REPLAY_MISMATCH_COUNT_TOTAL).inc()

    return ReplayResult(match=match, diff=diff)


__all__ = [
    "ReplayResult",
    "DecisionSource",
    "BundleSource",
    "DecisionNotFoundError",
    "BundleNotFoundError",
    "INTEGRITY_RISK_REPLAY_MISMATCH",
    "replay",
]
