"""Risk & Safety Gate API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

78번 §4 "Risk evaluation is internal service API, not a client-supplied
approval" — /evaluate는 조회 트리거일 뿐 클라이언트가 outcome을 실어보내는
필드가 없다(RSK-006 "agent/router cannot construct an ALLOW")."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_admin, get_current_user
from src.api.foundation_deps import (
    get_connection_repository,
    get_mandate_repository,
    get_risk_gate_repository,
)
from src.api.schemas.foundation.risk_gate import (
    ActivateSafetyControlRequest,
    EvaluateRiskGateRequest,
    RiskEvaluationView,
    SafetyControlListResponse,
    SafetyControlView,
)
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.risk_gate.application.activate_safety_control import (
    UnauthorizedSafetyControlScopeError,
    activate_safety_control,
)
from src.foundation.risk_gate.application.deactivate_safety_control import (
    SafetyControlNotFoundError,
    deactivate_safety_control,
)
from src.foundation.risk_gate.application.evaluate_risk_gate import (
    CrossTenantConnectionReferenceError,
    evaluate_risk_gate,
)
from src.foundation.risk_gate.domain.models import GateKind, SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.foundation.risk_gate.projections import build_safety_control_list_view
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/risk-gate", tags=["foundation:risk-gate"])


@router.post("/evaluate")
async def post_evaluate(
    body: EvaluateRiskGateRequest,
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
) -> RiskEvaluationView:
    try:
        return await evaluate_risk_gate(
            repo,
            mandate_repo,
            connection_repo,
            tenant_id=user.user_id,
            gate_kind=GateKind(body.gate_kind.value),
            connection_id=body.connection_id,
        )
    except CrossTenantConnectionReferenceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 연결입니다.") from exc


@router.get("/safety-controls")
async def get_safety_controls(
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> SafetyControlListResponse:
    view = await build_safety_control_list_view(repo, user.user_id)
    return SafetyControlListResponse(controls=view.controls, as_of=view.as_of)


@router.post("/safety-controls", status_code=status.HTTP_201_CREATED)
async def post_activate_safety_control(
    body: ActivateSafetyControlRequest,
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> SafetyControlView:
    try:
        return await activate_safety_control(
            repo,
            tenant_id=user.user_id,
            actor_subject_id=user.user_id,
            actor_is_admin=user.is_platform_admin,
            scope=SafetyScope(body.scope.value),
            scope_ref=body.scope_ref,
            reason=body.reason,
        )
    except UnauthorizedSafetyControlScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/safety-controls/{control_id}:deactivate")
async def post_deactivate_safety_control(
    control_id: UUID,
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> SafetyControlView:
    try:
        return await deactivate_safety_control(
            repo,
            tenant_id=user.user_id,
            actor_is_admin=user.is_platform_admin,
            control_id=control_id,
        )
    except SafetyControlNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 통제입니다.") from exc
    except UnauthorizedSafetyControlScopeError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc


@router.post("/admin/safety-controls", status_code=status.HTTP_201_CREATED)
async def post_admin_activate_safety_control(
    body: ActivateSafetyControlRequest,
    admin: User = Depends(get_current_admin),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> SafetyControlView:
    """78번 §4 "Only authorized operator ... routes may create scoped safety
    controls" 중 GLOBAL/TENANT/PROVIDER 범위 전용 경로 — 위 self-service
    엔드포인트와 분리해, 운영자 권한 없이는 라우팅 자체가 되지 않게 한다
    (RSK-006과 같은 원칙 — 권한 체크를 애플리케이션 로직에만 맡기지
    않는다)."""
    return await activate_safety_control(
        repo,
        tenant_id=admin.user_id,
        actor_subject_id=admin.user_id,
        actor_is_admin=True,
        scope=SafetyScope(body.scope.value),
        scope_ref=body.scope_ref,
        reason=body.reason,
    )
