"""Portfolio Mandate 계약 v1.

Spec: AIOSproject 45_portfolio_mandate_and_policy_specification_v1.0.md,
75_portfolio_mandate_l3_build_and_operational_specification_v1.0.md §3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

다른 bounded context(risk_gate, paper_control 등)는 이 파일을 소비하고,
domain/models.py를 직접 참조하지 않는다(71번 §4, 106번 §5).
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator

SCHEMA_VERSION = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str) -> str:
    """Shape check only (64 lowercase hex chars) — never recomputes a digest.

    Any new hash for these fields must go through
    `src.core.risk.hashing.sha256_hex`/`canonical_json` (R-01); this module
    does not reimplement sha256 hashing.
    """
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError("must be a lowercase sha256 hex digest (64 hex chars)")
    return value


def _validate_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("must be tz-aware (naive datetime rejected)")
    return value


class MandateRevisionState(str, Enum):
    DRAFT = "DRAFT"
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class Autonomy(str, Enum):
    OBSERVE = "OBSERVE"
    PAPER = "PAPER"
    LIMITED_LIVE = "LIMITED_LIVE"


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REQUIRE_REASSESSMENT = "REQUIRE_REASSESSMENT"
    PAUSE_REQUIRED = "PAUSE_REQUIRED"


class MandateRuleInput(BaseModel):
    """CreateMandateDraft/ProposeAmendment의 입력 — 75번 §3 6개 규칙."""

    max_total_exposure_pct: float
    max_single_instrument_pct: float
    min_cash_buffer_pct: float
    max_daily_loss_pct: float
    allowed_autonomy: Autonomy
    forbidden_assets: list[str] = []


class MandateRevisionView(BaseModel):
    id: UUID
    mandate_id: UUID
    revision_no: int
    state: MandateRevisionState
    max_total_exposure_pct: float
    max_single_instrument_pct: float
    min_cash_buffer_pct: float
    max_daily_loss_pct: float
    allowed_autonomy: Autonomy
    forbidden_assets: list[str]
    revision_hash: str
    cooling_off_started_at: datetime | None
    created_at: datetime | None
    activated_at: datetime | None
    schema_version: str = SCHEMA_VERSION


class PolicyEvaluationSubject(BaseModel):
    """71번 §4 Contract ownership — risk_gate/execution 등 소비자는 이 타입으로만
    EvaluatePolicy를 호출한다(75번 §3 "typed, not arbitrary JSON")."""

    command_type: str
    instrument_exposure_pct: float | None = None
    total_exposure_pct: float | None = None
    cash_buffer_pct: float | None = None
    projected_daily_loss_pct: float | None = None
    requested_autonomy: Autonomy | None = None
    asset: str | None = None


class PolicyDecisionView(BaseModel):
    id: UUID
    tenant_id: UUID
    bundle_id: UUID
    command_type: str
    outcome: PolicyOutcome
    reason_codes: list[str]
    obligations: list[str]
    evaluated_at: datetime
    expires_at: datetime | None
    schema_version: str = SCHEMA_VERSION


class ComplianceVerdict(str, Enum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    DENY = "DENY"


class RuleHit(BaseModel):
    """L4_compliance_and_regulatory_v1.0.md §3 RuleHit.

    CM-1 does not run a rule engine yet — the real `domain/rules/*.py`
    modules (one rule per file, pure functions returning `RuleHit | None`)
    ship in CM-3/CM-6/CM-7/CM-9. Until then, each RuleHit here is derived
    1:1 from an existing `policy_decision.reason_codes` entry, so
    `message`/`evidence` are direct passthroughs rather than structured
    rule output.
    """

    rule_id: str
    severity: ComplianceVerdict
    message: str
    evidence: dict[str, Any] = {}


class ComplianceDecision(BaseModel):
    """L4_compliance_and_regulatory_v1.0.md §3/§9 CM-1.

    Maps 1:1 onto the existing `policy_decision` row — CM-1 explicitly
    forbids a new table (§9 CM-1 decision note), so every field here is
    derivable from `policy_decision`/`policy_bundle` columns that already
    exist. See `compliance_decision_from_policy_decision` below for the
    mapping.
    """

    decision_id: UUID
    verdict: ComplianceVerdict
    rule_hits: list[RuleHit]
    inputs_hash: str
    bundle_version: str
    evaluated_at: datetime
    schema_version: str = SCHEMA_VERSION

    @field_validator("inputs_hash", "bundle_version")
    @classmethod
    def _check_hex(cls, value: str) -> str:
        return _validate_sha256_hex(value)

    @field_validator("evaluated_at")
    @classmethod
    def _check_tz(cls, value: datetime) -> datetime:
        return _validate_tz_aware(value)


class PolicyDecisionRow(BaseModel):
    """Source data for `compliance_decision_from_policy_decision` — a plain
    value object, not a new table. Every field already exists on the
    domain `PolicyDecision`/`PolicyBundle` rows (75번 §1/§3); this type only
    bundles them so the mapper below takes a single one-line argument
    instead of a per-field keyword signature.
    """

    decision_id: UUID
    outcome: PolicyOutcome
    reason_codes: list[str]
    inputs_hash: str
    bundle_version: str
    evaluated_at: datetime

    @field_validator("inputs_hash", "bundle_version")
    @classmethod
    def _check_hex(cls, value: str) -> str:
        return _validate_sha256_hex(value)

    @field_validator("evaluated_at")
    @classmethod
    def _check_tz(cls, value: datetime) -> datetime:
        return _validate_tz_aware(value)


# `policy_decision.outcome` has two states — REQUIRE_APPROVAL and
# REQUIRE_REASSESSMENT — that predate CM-1 and are not yet produced by
# `evaluate_policy()` (see mandates/domain/rules.py); both collapse to WARN
# below. PAUSE_REQUIRED collapses to DENY: it blocks the normal order flow
# the same way DENY does, so mapping it to anything short of DENY would
# violate the fail-closed default.
_OUTCOME_TO_VERDICT: dict[PolicyOutcome, ComplianceVerdict] = {
    PolicyOutcome.ALLOW: ComplianceVerdict.ALLOW,
    PolicyOutcome.DENY: ComplianceVerdict.DENY,
    PolicyOutcome.REQUIRE_APPROVAL: ComplianceVerdict.WARN,
    PolicyOutcome.REQUIRE_REASSESSMENT: ComplianceVerdict.WARN,
    PolicyOutcome.PAUSE_REQUIRED: ComplianceVerdict.DENY,
}


def verdict_for_outcome(outcome: PolicyOutcome) -> ComplianceVerdict:
    """Total, deterministic `PolicyOutcome` -> `ComplianceVerdict` mapping.

    Exposed separately from `compliance_decision_from_policy_decision` so
    the mapping table itself can be asserted directly in tests.
    """
    return _OUTCOME_TO_VERDICT[outcome]


def compliance_decision_from_policy_decision(row: PolicyDecisionRow) -> ComplianceDecision:
    """Project one `PolicyDecisionRow` onto CM-1's `ComplianceDecision`.

    `row.reason_codes` becomes one `RuleHit` each; `policy_decision.
    obligations` has no field in `ComplianceDecision` per §3 and is
    intentionally dropped by callers building the row.
    """
    verdict = verdict_for_outcome(row.outcome)
    rule_hits = [
        RuleHit(rule_id=code, severity=verdict, message=code, evidence={})
        for code in row.reason_codes
    ]
    return ComplianceDecision(
        decision_id=row.decision_id,
        verdict=verdict,
        rule_hits=rule_hits,
        inputs_hash=row.inputs_hash,
        bundle_version=row.bundle_version,
        evaluated_at=row.evaluated_at,
    )
