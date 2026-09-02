"""ActivateSafetyControl 커맨드(kill switch).

Spec: AIOSproject 48번 §4, 78번 §3/§4.

78번 §4 "Only authorized operator/risk policy routes may create scoped
safety controls; user Control Center can invoke permitted pause scope." —
GLOBAL/PROVIDER/TENANT는 운영자 권한이 필요하고, ACCOUNT(자기 자신 계좌
정지)만 일반 사용자에게 열려 있다. STRATEGY_DEPLOYMENT는 FND-07(아직 없음)
없이는 의미 있는 scope_ref가 없어 이 리프에서는 운영자 전용으로 취급한다
(향후 FND-07이 생기면 배포 소유자에게도 열 수 있다).
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.risk_gate.contracts.v1 import SafetyControlState as ContractState
from src.foundation.risk_gate.contracts.v1 import SafetyControlView
from src.foundation.risk_gate.contracts.v1 import SafetyScope as ContractScope
from src.foundation.risk_gate.domain.models import (
    GLOBAL_SCOPE_REF,
    SafetyControl,
    SafetyScope,
)
from src.foundation.risk_gate.ports.repository import RiskGateRepository

_SELF_SERVICE_SCOPES = frozenset({SafetyScope.ACCOUNT})


class UnauthorizedSafetyControlScopeError(Exception):
    pass


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
) -> SafetyControlView:
    if scope == SafetyScope.GLOBAL:
        resolved_ref = GLOBAL_SCOPE_REF
    else:
        resolved_ref = scope_ref or ""

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
    return control_to_view(control)
