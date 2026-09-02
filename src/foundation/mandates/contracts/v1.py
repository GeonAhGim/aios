"""Portfolio Mandate 계약 v1.

Spec: AIOSproject 45_portfolio_mandate_and_policy_specification_v1.0.md,
75_portfolio_mandate_l3_build_and_operational_specification_v1.0.md §3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

다른 bounded context(risk_gate, paper_control 등)는 이 파일을 소비하고,
domain/models.py를 직접 참조하지 않는다(71번 §4, 106번 §5).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


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
