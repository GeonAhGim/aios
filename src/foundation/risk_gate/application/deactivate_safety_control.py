"""DeactivateSafetyControl 커맨드.

Spec: AIOSproject 78번 §3 "DeactivateSafetyControl only makes recovery
review possible and cannot create RUNNING state" — 이 코드베이스엔 아직
paper_control(FND-07)이 없어 "RUNNING 상태를 만들 수 없다"는 제약이 자연히
지켜진다(만들 대상 자체가 없음). 이 커맨드는 그저 control을 INACTIVE로
표시할 뿐, 정지됐던 무언가를 다시 켜지 않는다.
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.risk_gate.application.activate_safety_control import (
    UnauthorizedSafetyControlScopeError,
    control_to_view,
)
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.domain.models import SafetyScope
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
    return control_to_view(deactivated)
