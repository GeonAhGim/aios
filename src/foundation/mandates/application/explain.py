"""L4_compliance_and_regulatory_v1.0.md#9 CM-13 — reproduce a stored
`policy_decision` as a `ComplianceDecision`, byte-for-byte, on every call.

CM-4 already made `policy_decision`/`policy_bundle` WORM (append-only) rows,
and CM-1's `compliance_decision_from_policy_decision()` (contracts/v1.py) is
the sole `PolicyDecisionRow -> ComplianceDecision` mapping. This module does
not re-run `evaluate_policy()` (application/evaluate_policy.py) or
`evaluate_bundle()` (domain/evaluator.py), and does not add a second hashing
or mapping path — it only (1) reads the two immutable rows back via the
existing `MandateRepository` port, (2) proves the bundle the decision was
made under has not silently drifted from what today's compiler would
produce for the same `mandate_revision`, and (3) replays CM-1's mapper.
None of these three steps touch the clock or randomness, so two calls with
the same `decision_id` always return a model that serializes to the same
JSON bytes: dict order is fixed by pydantic's field-declaration order, and
every value (hashes, reason codes, `evaluated_at`) is taken verbatim from
the stored rows instead of being recomputed.
"""
from __future__ import annotations

from enum import Enum
from uuid import UUID

from src.foundation.mandates.contracts.v1 import (
    ComplianceDecision,
    PolicyDecisionRow,
    compliance_decision_from_policy_decision,
)
from src.foundation.mandates.contracts.v1 import PolicyOutcome as ContractOutcome
from src.foundation.mandates.domain.rules import compile_rule_hash
from src.foundation.mandates.ports.repository import MandateRepository


class ExplainErrorCode(str, Enum):
    DECISION_NOT_FOUND = "CM_EXPLAIN_DECISION_NOT_FOUND"
    BUNDLE_DRIFTED = "CM_EXPLAIN_BUNDLE_DRIFTED"


class ExplainError(Exception):
    """The only failure mode of `explain()` — so callers (e.g. the API router) can
    map `code` to an HTTP status, this rejects only with this type, never leaking
    a nonexistent id or integrity violation as a raw exception (KeyError/AttributeError, etc.)
    (§9 CM-13 DoD (b)/(c))."""

    def __init__(self, code: ExplainErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


async def explain(repo: MandateRepository, decision_id: UUID) -> ComplianceDecision:
    """§9 CM-13 public contract. Calling this any number of times with the same
    `decision_id` returns the identical `ComplianceDecision` — it does not re-evaluate
    rules, but replays the stored `policy_decision`/`policy_bundle` rows as-is."""
    decision = await repo.get_policy_decision(decision_id)
    if decision is None:
        raise ExplainError(
            ExplainErrorCode.DECISION_NOT_FOUND,
            f"policy_decision {decision_id} does not exist",
        )

    bundle = await repo.get_bundle(decision.bundle_id)
    if bundle is None:
        # policy_decision.bundle_id has a DB FK to policy_bundle, so this is
        # unreachable through the normal write path — but explain() must
        # still fail closed here instead of crashing on `bundle.rule_hash`.
        raise ExplainError(
            ExplainErrorCode.BUNDLE_DRIFTED,
            f"policy_bundle {decision.bundle_id} referenced by decision {decision_id} is missing",
        )

    revision = await repo.get_revision(bundle.mandate_revision_id)
    if revision is None:
        raise ExplainError(
            ExplainErrorCode.BUNDLE_DRIFTED,
            f"mandate_revision {bundle.mandate_revision_id} for bundle {bundle.id} is missing",
        )

    current_rule_hash = compile_rule_hash(revision)
    if current_rule_hash != bundle.rule_hash:
        # Stored bundle hash disagrees with what today's compiler produces
        # for the same revision (e.g. compiler logic changed without a data
        # migration). Silently recomputing here would mean two different
        # judgments get reported as "the same decision" — instead this
        # rejects, per §9 CM-13 DoD (b) "no quiet re-judgment".
        raise ExplainError(
            ExplainErrorCode.BUNDLE_DRIFTED,
            f"policy_bundle {bundle.id} rule_hash mismatch: "
            f"stored={bundle.rule_hash} current={current_rule_hash}",
        )

    row = PolicyDecisionRow(
        decision_id=decision.id,
        outcome=ContractOutcome(decision.outcome.value),
        reason_codes=list(decision.reason_codes),
        inputs_hash=decision.command_fingerprint,
        bundle_version=bundle.rule_hash,
        evaluated_at=decision.evaluated_at,
    )
    return compliance_decision_from_policy_decision(row)
