"""Risk & Safety Gate 계약 v1.

Spec: AIOSproject 48_risk_safety_gate_and_kill_switch_specification_v1.0.md,
78_risk_safety_l3_build_and_operational_specification_v1.0.md §2,
107_contract_versioning_and_compatibility_standard_v1.0.md.

71번 §4 Contract ownership — `RiskDecision`은 paper_control/order adapter가
소비하는 "adapter 전 final veto"다. 다른 컨텍스트는 이 파일을 소비하고
domain/models.py를 직접 참조하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class RiskOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REDUCE = "REDUCE"
    PAUSE = "PAUSE"
    ESCALATE = "ESCALATE"


class GateKind(str, Enum):
    """L4_risk_and_safety_v1.0.md §3.1/§6(`f4b9d6e5a7c8`) 6종 그대로."""

    DEPLOYMENT = "DEPLOYMENT"
    PRE_INTENT = "PRE_INTENT"
    PRE_TRADE = "PRE_TRADE"
    PRE_SUBMIT = "PRE_SUBMIT"
    INTRADAY = "INTRADAY"
    RECOVERY = "RECOVERY"


class SafetyScope(str, Enum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    ACCOUNT = "ACCOUNT"
    STRATEGY_DEPLOYMENT = "STRATEGY_DEPLOYMENT"
    PROVIDER = "PROVIDER"


class SafetyControlState(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class EvaluateRiskGateRequest(BaseModel):
    gate_kind: GateKind
    connection_id: UUID | None = None
    """지정하면 해당 connection의 freshness를 검사에 포함한다(78번 §1)."""


class RiskEvaluationView(BaseModel):
    id: UUID
    gate_kind: GateKind
    outcome: RiskOutcome
    reason_codes: list[str]
    obligations: list[str]
    rule_version: str
    evaluated_at: datetime
    expires_at: datetime | None
    trace_id: UUID | None = None
    schema_version: str = SCHEMA_VERSION


class ActivateSafetyControlRequest(BaseModel):
    scope: SafetyScope
    scope_ref: str | None = None
    reason: str


class SafetyControlView(BaseModel):
    id: UUID
    scope: SafetyScope
    scope_ref: str
    state: SafetyControlState
    reason: str
    fence_token: int
    created_at: datetime | None
    deactivated_at: datetime | None
    idempotency_digest: str | None = None
    schema_version: str = SCHEMA_VERSION
