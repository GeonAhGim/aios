"""Risk & Safety Gate 도메인 모델 — pure value object.

Spec: AIOSproject 78_risk_safety_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class RiskOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REDUCE = "REDUCE"
    PAUSE = "PAUSE"
    ESCALATE = "ESCALATE"


class GateKind(str, Enum):
    """48번 §3 5개 게이트 중 이 리프가 구현하는 두 개(71번 FND-06 최소
    산출물 "deployment/pre-intent checks"). pre-submit/intraday/recovery
    게이트는 실제 주문 제출 경로(FND-07/order adapter, 아직 없음)가 있어야
    의미가 있어 이 리프 스콥 밖이다."""

    DEPLOYMENT = "DEPLOYMENT"
    PRE_INTENT = "PRE_INTENT"


class SafetyScope(str, Enum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"
    ACCOUNT = "ACCOUNT"
    STRATEGY_DEPLOYMENT = "STRATEGY_DEPLOYMENT"
    PROVIDER = "PROVIDER"


class SafetyControlState(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


# 48번 §4 kill switch 범위 중 GLOBAL/PROVIDER는 scope_ref가 없을 수 있다
# (PROVIDER는 provider_code로 특정 가능하지만, "모든 provider 정지"는 GLOBAL이
# 담당). scope_ref가 없는 경우 이 상수를 쓴다 — NULL을 PK/UNIQUE에 쓰면
# Postgres에서 "NULL != NULL"이라 같은 GLOBAL 행이 여러 개 생길 수 있다.
GLOBAL_SCOPE_REF = ""


@dataclass(frozen=True)
class SafetyControl:
    id: UUID
    scope: SafetyScope
    scope_ref: str
    state: SafetyControlState
    reason: str
    actor_subject_id: UUID
    fence_token: int
    created_at: datetime | None = None
    deactivated_at: datetime | None = None


@dataclass(frozen=True)
class RiskEvaluation:
    id: UUID
    tenant_id: UUID
    gate_kind: GateKind
    subject_fingerprint: str
    outcome: RiskOutcome
    reason_codes: tuple[str, ...]
    obligations: tuple[str, ...]
    rule_version: str
    evaluated_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class RiskEvaluationInput:
    """evaluate_risk()의 순수 입력 — 78번 §1 "typed snapshots". 이미 판단이
    끝난 다른 컨텍스트의 산출물만 받는다(mandate PolicyDecision, connection
    freshness, 현재 활성 safety control) — risk_gate 자신은 원본 mandate/
    connection 행을 읽지 않는다(71번 §4 Contract ownership 경계 유지).

    mandate의 `PolicyOutcome`(ALLOW/DENY/REQUIRE_APPROVAL/REQUIRE_REASSESSMENT/
    PAUSE_REQUIRED)을 그대로 옮기지 않고 `mandate_blocking` bool로 단순화한다
    — risk_gate 입장에서는 "mandate가 통과시켰는가"만 의미가 있고, 그
    이유(reason_codes)는 그대로 전달해 최종 RiskDecision에 합친다."""

    mandate_available: bool
    """False면 활성 mandate 자체가 없다는 뜻(RSK-002 stale/missing input)."""
    mandate_blocking: bool = False
    mandate_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    connection_fresh: bool | None = None
    """None이면 이 게이트에 connection freshness가 해당 없음(예: connection이
    아직 없는 최초 mandate 평가)."""
    active_controls: tuple[SafetyControl, ...] = field(default_factory=tuple)
