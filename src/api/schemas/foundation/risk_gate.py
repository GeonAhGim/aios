"""Risk & Safety Gate API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약
자체는 `src/foundation/risk_gate/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from datetime import datetime

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
