"""Risk & Safety Gate 도메인 모델 — pure value object.

Spec: AIOSproject 78_risk_safety_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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

# 레드팀 #2026-09-02-26 — 이 범위의 control이 활성화/비활성화되면 특정
# tenant 하나가 아니라 시스템 전체(모든 tenant)의 캐시된 risk_evaluation을
# 무효화해야 한다(activate/deactivate_safety_control.py가 공유).
SYSTEM_WIDE_SCOPES = frozenset({SafetyScope.GLOBAL, SafetyScope.PROVIDER})


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


@dataclass(frozen=True)
class FenceSnapshot:
    """78번 §3.6 fence 관통 제출 시퀀스의 F0/F1/F2 — (scope, scope_ref)별
    단조증가 토큰의 한 시점 관측. 행이 아직 없는 (scope, scope_ref)는 토큰
    0(한 번도 activate된 적 없음)으로 채워져 들어온다(read_fences 어댑터
    책임) — 그래야 `is_stale`이 "관측 안 됨"과 "0"을 같은 값으로 비교한다."""

    tokens: Mapping[tuple[SafetyScope, str], int]


class LimitScope(str, Enum):
    """R-26 78번 §2.6 노출 한도 스코프 — `SafetyScope`와 이름이 겹치는 항목이
    있어도(TENANT/ACCOUNT/PROVIDER) 별개 enum이다. 이쪽은 "어떤 대상에 한도가
    걸리는가"이고 `SafetyScope`는 "킬스위치가 어디를 멈추는가"라 의미가 다르고,
    이 enum에만 있는 STRATEGY/SYMBOL/ASSET_CLASS도 있어 재사용하면 둘 다
    왜곡된다."""

    TENANT = "TENANT"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    ASSET_CLASS = "ASSET_CLASS"
    PROVIDER = "PROVIDER"


class LimitMetric(str, Enum):
    GROSS_NOTIONAL_PCT = "GROSS_NOTIONAL_PCT"
    NET_NOTIONAL_PCT = "NET_NOTIONAL_PCT"
    MAX_ORDER_NOTIONAL = "MAX_ORDER_NOTIONAL"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    MAX_TRADES_PER_HOUR = "MAX_TRADES_PER_HOUR"
    MAX_LEVERAGE = "MAX_LEVERAGE"


class LimitBreachSeverity(str, Enum):
    WARN = "WARN"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class RiskLimit:
    """`risk_limit` 1행 — 78번 §2.6 표 그대로. `tenant_id=None`은 플랫폼
    기본값(모든 tenant에 적용, tenant별 한도가 따로 있으면 그쪽이 우선한다는
    뜻은 이 모델이 아니라 `list_effective` 조회 로직의 책임).

    `updated_at`은 이중 역할이다 — 저장소에 넘길 때는 호출자가 마지막으로
    읽은 낙관적 잠금 기대값(신규 생성이면 `None`), 저장소가 돌려줄 때는 DB가
    실제로 기록한 새 타임스탬프. `upsert_risk_limit.py`/
    `postgres_limit_repository.upsert()` docstring 참고."""

    id: UUID
    tenant_id: UUID | None
    scope: LimitScope
    scope_ref: str
    metric: LimitMetric
    limit_value: Decimal
    hard: bool = True
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    created_by: UUID | None = None
    approval_ref: str | None = None
    updated_at: datetime | None = None
