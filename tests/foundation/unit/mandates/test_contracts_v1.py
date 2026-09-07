"""L4_compliance_and_regulatory_v1.0.md §9 CM-1 — ComplianceDecision/RuleHit
contract tests. No DB, no new table: these map onto the existing
`policy_decision`/`policy_bundle` rows only.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.hashing import canonical_json, sha256_hex
from src.foundation.mandates.contracts.v1 import (
    ComplianceDecision,
    ComplianceVerdict,
    PolicyDecisionRow,
    PolicyOutcome,
    RuleHit,
    compliance_decision_from_policy_decision,
    verdict_for_outcome,
)

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


def _hex_digest(seed: str) -> str:
    # R-01 reuse (canonical_json + sha256_hex) — never hand-rolled hashlib.
    return sha256_hex(canonical_json({"seed": seed}))


def test_rejects_verdict_outside_allow_warn_deny() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict="ALLOWED",  # not in the ALLOW|WARN|DENY table
            rule_hits=[],
            inputs_hash=_hex_digest("inputs"),
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_rejects_non_sha256_inputs_hash() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict=ComplianceVerdict.ALLOW,
            rule_hits=[],
            inputs_hash="not-a-hash",
            bundle_version=_hex_digest("bundle"),
            evaluated_at=NOW,
        )


def test_rejects_naive_evaluated_at() -> None:
    with pytest.raises(ValidationError):
        ComplianceDecision(
            decision_id=uuid4(),
            verdict=ComplianceVerdict.ALLOW,
            rule_hits=[],
            inputs_hash=_hex_digest("inputs"),
            bundle_version=_hex_digest("bundle"),
            evaluated_at=datetime(2026, 9, 7),  # naive
        )


def test_outcome_to_verdict_mapping_is_total_and_fail_closed() -> None:
    assert verdict_for_outcome(PolicyOutcome.ALLOW) == ComplianceVerdict.ALLOW
    assert verdict_for_outcome(PolicyOutcome.DENY) == ComplianceVerdict.DENY
    assert verdict_for_outcome(PolicyOutcome.REQUIRE_APPROVAL) == ComplianceVerdict.WARN
    assert verdict_for_outcome(PolicyOutcome.REQUIRE_REASSESSMENT) == ComplianceVerdict.WARN
    # PAUSE_REQUIRED blocks the order flow just like DENY — must not degrade to WARN.
    assert verdict_for_outcome(PolicyOutcome.PAUSE_REQUIRED) == ComplianceVerdict.DENY


def test_round_trip_from_existing_policy_decision_row() -> None:
    """Simulates one existing `policy_decision` row (as read from Postgres)
    and asserts every ComplianceDecision field is equivalent to the source
    row's field — no new table, no silent data loss for the fields CM-1's
    contract carries.
    """
    row_id = uuid4()
    row_command_fingerprint = _hex_digest("tenant+subject+asset")
    row_reason_codes = ["POLICY_MAX_TOTAL_EXPOSURE", "POLICY_MAX_SINGLE_INSTRUMENT"]
    bundle_rule_hash = _hex_digest("revision-rules")

    row = PolicyDecisionRow(
        decision_id=row_id,
        outcome=PolicyOutcome.DENY,
        reason_codes=row_reason_codes,
        inputs_hash=row_command_fingerprint,
        bundle_version=bundle_rule_hash,
        evaluated_at=NOW,
    )
    decision = compliance_decision_from_policy_decision(row)

    assert decision.decision_id == row_id
    assert decision.inputs_hash == row_command_fingerprint
    assert decision.bundle_version == bundle_rule_hash
    assert decision.evaluated_at == NOW
    assert decision.verdict == ComplianceVerdict.DENY
    assert [hit.rule_id for hit in decision.rule_hits] == row_reason_codes
    assert all(hit.severity == ComplianceVerdict.DENY for hit in decision.rule_hits)
    assert all(isinstance(hit, RuleHit) for hit in decision.rule_hits)


def test_round_trip_allow_outcome_has_no_rule_hits() -> None:
    row_id = uuid4()
    row_command_fingerprint = _hex_digest("allow-case")
    bundle_rule_hash = _hex_digest("revision-rules-allow")

    row = PolicyDecisionRow(
        decision_id=row_id,
        outcome=PolicyOutcome.ALLOW,
        reason_codes=[],
        inputs_hash=row_command_fingerprint,
        bundle_version=bundle_rule_hash,
        evaluated_at=NOW,
    )
    decision = compliance_decision_from_policy_decision(row)

    assert decision.decision_id == row_id
    assert decision.verdict == ComplianceVerdict.ALLOW
    assert decision.rule_hits == []
    assert decision.inputs_hash == row_command_fingerprint
    assert decision.bundle_version == bundle_rule_hash
