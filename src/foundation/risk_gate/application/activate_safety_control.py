"""ActivateSafetyControl 커맨드(kill switch).

Spec: AIOSproject 48번 §4, 78번 §3/§4.

78번 §4 "Only authorized operator/risk policy routes may create scoped
safety controls; user Control Center can invoke permitted pause scope." —
GLOBAL/PROVIDER/TENANT는 운영자 권한이 필요하고, ACCOUNT(자기 자신 계좌
정지)만 일반 사용자에게 열려 있다. STRATEGY_DEPLOYMENT는 FND-07(아직 없음)
없이는 의미 있는 scope_ref가 없어 이 리프에서는 운영자 전용으로 취급한다
(향후 FND-07이 생기면 배포 소유자에게도 열 수 있다) — 지금 생성해도
`evaluate_risk_gate()`가 이 범위를 조회하지 않아(#2026-09-02-28) 아직
어떤 평가에도 영향을 주지 못한다. 의도된 선반영이지 버그는 아니지만,
호출자는 "생성 성공 = 즉시 집행" 으로 오해하면 안 된다.

레드팀 #2026-09-02-30 — 이 모듈이 만드는 킬스위치는 현재 신규 배포
시작(`paper_control.start_deployment`/`resume_deployment`)만 막는다.
이미 RUNNING인 배포가 계속 주문을 내는 것은 이 게이트로 막지 못한다
(주문 제출 경로 자체가 FND-07 이전이라 이 모듈에 연결돼 있지 않음,
`c7d4e1a9f052_foundation_risk_gate.py` 마이그레이션 주석 참조) —
기존 실행-레벨 킬스위치(레드팀 #08 대상, execution_loop 쪽)와는 별개
시스템이다.
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification as AuditClassification
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.risk_gate.contracts.v1 import SafetyControlState as ContractState
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.contracts.v1 import SafetyScope as ContractScope
from src.foundation.risk_gate.domain.models import (
    GLOBAL_SCOPE_REF,
    SYSTEM_WIDE_SCOPES,
    SafetyControl,
    SafetyScope,
)
from src.foundation.risk_gate.ports.repository import RiskGateRepository

_SELF_SERVICE_SCOPES = frozenset({SafetyScope.ACCOUNT})
# 레드팀 #2026-09-02-29 — scope_ref 없이는 절대 매치될 수 없는(고아) control
# 행이 만들어지는 범위. GLOBAL만 예외(자체 상수 GLOBAL_SCOPE_REF 사용).
_SCOPE_REF_REQUIRED = frozenset(
    {SafetyScope.TENANT, SafetyScope.PROVIDER, SafetyScope.STRATEGY_DEPLOYMENT}
)


class UnauthorizedSafetyControlScopeError(Exception):
    pass


class MissingScopeRefError(Exception):
    """TENANT/PROVIDER/STRATEGY_DEPLOYMENT 범위인데 scope_ref가 없음 —
    라우터가 400으로 변환. 방치하면 절대 매치되지 않는 고아 control이
    조용히 생성된다(#2026-09-02-29)."""


def control_to_view(control: SafetyControl) -> SafetyControlView:
    return SafetyControlView(
        id=control.id,
        scope=ContractScope(control.scope.value),
        scope_ref=control.scope_ref,
        state=ContractState(control.state.value),
        reason=control.reason,
        fence_token=control.fence_token,
        created_at=control.created_at,
        deactivated_at=control.deactivated_at,
        idempotency_digest=control.idempotency_digest,
    )


async def activate_safety_control(
    repo: RiskGateRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    actor_is_admin: bool,
    scope: SafetyScope,
    scope_ref: str | None,
    reason: str,
    audit_repo: AuditEventRepository | None = None,
) -> SafetyControlView:
    if scope == SafetyScope.GLOBAL:
        resolved_ref = GLOBAL_SCOPE_REF
    else:
        resolved_ref = scope_ref or ""

    if scope in _SCOPE_REF_REQUIRED and not resolved_ref:
        raise MissingScopeRefError(
            f"{scope.value} 범위는 scope_ref가 필요합니다 — 없으면 어떤 평가에도 "
            "매치되지 않는 통제가 만들어집니다."
        )

    if not actor_is_admin:
        if scope not in _SELF_SERVICE_SCOPES:
            raise UnauthorizedSafetyControlScopeError(
                f"{scope.value} 범위의 안전 통제는 운영자만 걸 수 있습니다."
            )
        if resolved_ref != str(tenant_id):
            raise UnauthorizedSafetyControlScopeError(
                "본인 계좌 외의 범위는 지정할 수 없습니다."
            )

    control = await repo.insert_safety_control(
        scope=scope, scope_ref=resolved_ref, reason=reason, actor_subject_id=actor_subject_id
    )

    # 레드팀 #2026-09-02-26 — 캐시 TTL(10초) 만료를 기다리지 않고 이 통제가
    # 즉시 반영되도록 무효화한다.
    if scope in SYSTEM_WIDE_SCOPES:
        await repo.invalidate_evaluations(tenant_id=None)
    elif scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT):
        await repo.invalidate_evaluations(tenant_id=UUID(resolved_ref))
    # STRATEGY_DEPLOYMENT: 이 범위를 조회하는 평가 경로 자체가 아직 없어
    # (#2026-09-02-28) 무효화할 캐시도 없다 — 의도적으로 건너뜀.

    if audit_repo is not None:
        # GLOBAL/PROVIDER/STRATEGY_DEPLOYMENT는 특정 tenant 하나에 귀속되지
        # 않는다(79번 §1 "tenant_id가 None이면 system 이벤트") — TENANT/
        # ACCOUNT만 resolved_ref 자체가 대상 tenant_id다.
        event_tenant_id = (
            UUID(resolved_ref) if scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT) else None
        )
        await record_command_event(
            audit_repo,
            tenant_id=event_tenant_id,
            aggregate_type="safety_control",
            aggregate_id=control.id,
            action="safety_control_activated",
            actor_subject_id=actor_subject_id,
            classification=AuditClassification.CONFIDENTIAL,
            payload={"scope": scope.value, "scope_ref": resolved_ref, "reason": reason},
        )

    return control_to_view(control)
