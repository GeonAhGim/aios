"""DeactivateSafetyControl 커맨드.

Spec: AIOSproject 78번 §3 "DeactivateSafetyControl only makes recovery
review possible and cannot create RUNNING state" — 이 코드베이스엔 아직
paper_control(FND-07)이 없어 "RUNNING 상태를 만들 수 없다"는 제약이 자연히
지켜진다(만들 대상 자체가 없음). 이 커맨드는 그저 control을 INACTIVE로
표시할 뿐, 정지됐던 무언가를 다시 켜지 않는다.
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.contracts.v1 import Classification as AuditClassification
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.risk_gate.application.activate_safety_control import (
    UnauthorizedSafetyControlScopeError,
    control_to_view,
)
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.domain.models import SYSTEM_WIDE_SCOPES, SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository

_SELF_SERVICE_SCOPES = frozenset({SafetyScope.ACCOUNT})


class SafetyControlNotFoundError(Exception):
    pass


async def deactivate_safety_control(
    repo: RiskGateRepository,
    *,
    tenant_id: UUID,
    actor_is_admin: bool,
    control_id: UUID,
    audit_repo: AuditEventRepository | None = None,
) -> SafetyControlView:
    control = await repo.get_safety_control(control_id)
    if control is None:
        raise SafetyControlNotFoundError(str(control_id))

    if not actor_is_admin:
        if control.scope not in _SELF_SERVICE_SCOPES or control.scope_ref != str(tenant_id):
            raise UnauthorizedSafetyControlScopeError(
                "본인 계좌 범위의 안전 통제만 해제할 수 있습니다."
            )

    deactivated = await repo.deactivate_safety_control(control_id)

    # 레드팀 #2026-09-02-26과 동일 원칙 — 해제도 즉시 반영돼야 한다(예:
    # 실수로 건 GLOBAL 킬스위치를 급히 해제했는데 10초간 여전히 막혀
    # 있으면 그 자체가 새로운 안전 문제가 된다).
    if control.scope in SYSTEM_WIDE_SCOPES:
        await repo.invalidate_evaluations(tenant_id=None)
    elif control.scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT):
        await repo.invalidate_evaluations(tenant_id=UUID(control.scope_ref))

    if audit_repo is not None:
        event_tenant_id = (
            UUID(control.scope_ref)
            if control.scope in (SafetyScope.TENANT, SafetyScope.ACCOUNT)
            else None
        )
        await record_command_event(
            audit_repo,
            tenant_id=event_tenant_id,
            aggregate_type="safety_control",
            aggregate_id=control.id,
            action="safety_control_deactivated",
            actor_subject_id=tenant_id,
            classification=AuditClassification.CONFIDENTIAL,
            payload={"scope": control.scope.value, "scope_ref": control.scope_ref},
        )

    return control_to_view(deactivated)
