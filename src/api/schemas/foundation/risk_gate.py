"""Risk & Safety Gate API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약
자체는 `src/foundation/risk_gate/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.foundation.risk_gate.contracts.v1 import (
    ActivateSafetyControlRequest,
    EvaluateRiskGateRequest,
    GateKind,
    RiskEvaluationView,
    RiskOutcome,
    SafetyControlState,
    SafetyControlView,
    SafetyScope,
)

__all__ = [
    "ActivateSafetyControlRequest",
    "ApproveRuleBundleRequest",
    "EvaluateRiskGateRequest",
    "GateKind",
    "RecoverySafetyControlRequest",
    "RecoveryDecisionView",
    "RiskEvaluationView",
    "RiskOutcome",
    "SafetyControlListResponse",
    "SafetyControlState",
    "SafetyControlView",
    "SafetyScope",
]


class SafetyControlListResponse(BaseModel):
    controls: list[SafetyControlView]
    as_of: datetime


class ApproveRuleBundleRequest(BaseModel):
    approval_ref: str


class RecoverySafetyControlRequest(BaseModel):
    """R-53 — RECOVERY 게이트(§9). `evidence_ref=None`은 거부 사유
    RSK-007로 이어진다(evaluate_recovery가 판정, 여기서는 필드만 옮긴다)."""

    evidence_ref: str | None = None
    approval_id: int


class RecoveryDecisionView(BaseModel):
    id: UUID
    gate_kind: GateKind
    outcome: RiskOutcome
    reason_codes: list[str]
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
