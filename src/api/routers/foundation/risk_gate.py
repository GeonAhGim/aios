"""Risk & Safety Gate API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

78번 §4 "Risk evaluation is internal service API, not a client-supplied
approval" — /evaluate는 조회 트리거일 뿐 클라이언트가 outcome을 실어보내는
필드가 없다(RSK-006 "agent/router cannot construct an ALLOW").

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(§9 PLT-21b decision,
task-1218)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_admin, get_current_user
from src.api.foundation_deps import (
    get_audit_event_repository,
    get_connection_repository,
    get_mandate_repository,
    get_paper_control_repository,
    get_risk_gate_repository,
    get_rule_bundle_repository,
)
from src.api.schemas.foundation.risk_gate import (
    ActivateSafetyControlRequest,
    ApproveRuleBundleRequest,
    EvaluateRiskGateRequest,
    RiskEvaluationView,
    SafetyControlListResponse,
    SafetyControlView,
)
from src.core.risk.policy_bundle import RiskRuleBundle
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.apply_safety_control import (
    apply_safety_control_to_deployments,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.risk_gate.application.activate_rule_bundle import (
    activate_rule_bundle,
    approve_rule_bundle,
)
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.deactivate_safety_control import (
    deactivate_safety_control,
)
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind, SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository, RuleBundleRepository
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
) -> ApiResponse[RiskEvaluationView]:
    result = await evaluate_risk_gate(
        repo,
        mandate_repo,
        connection_repo,
        tenant_id=user.user_id,
        gate_kind=GateKind(body.gate_kind.value),
        connection_id=body.connection_id,
    )
    return ok(result)


@router.get("/safety-controls")
async def get_safety_controls(
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> ApiResponse[SafetyControlListResponse]:
    view = await build_safety_control_list_view(repo, user.user_id)
    return ok(SafetyControlListResponse(controls=view.controls, as_of=view.as_of))


@router.post("/safety-controls", status_code=status.HTTP_201_CREATED)
async def post_activate_safety_control(
    body: ActivateSafetyControlRequest,
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
    paper_control_repo: PaperControlRepository = Depends(get_paper_control_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[SafetyControlView]:
    control = await activate_safety_control(
        repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        actor_is_admin=user.is_platform_admin,
        scope=SafetyScope(body.scope.value),
        scope_ref=body.scope_ref,
        reason=body.reason,
        audit_repo=audit_repo,
    )
    # 교차세션 감사 발견(agent-platform-12) 반영 — kill switch가 이미 RUNNING인
    # 배포를 실제로 PAUSED로 전이시킨다(apply_safety_control.py 참조). 이 호출이
    # 실패해도 위 control 생성 자체는 이미 커밋됐다 — kill switch는 걸렸으니
    # 최소한 새 제출은 막힌다(PRE_INTENT 게이트, submit_paper_intent.py).
    await apply_safety_control_to_deployments(
        paper_control_repo,
        scope=SafetyScope(control.scope.value),
        scope_ref=control.scope_ref,
        safety_control_id=control.id,
        actor_subject_id=user.user_id,
        reason=body.reason,
    )
    return ok(control)


@router.post("/safety-controls/{control_id}:deactivate")
async def post_deactivate_safety_control(
    control_id: UUID,
    user: User = Depends(get_current_user),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[SafetyControlView]:
    result = await deactivate_safety_control(
        repo,
        tenant_id=user.user_id,
        actor_is_admin=user.is_platform_admin,
        control_id=control_id,
        audit_repo=audit_repo,
    )
    return ok(result)


@router.post("/admin/safety-controls", status_code=status.HTTP_201_CREATED)
async def post_admin_activate_safety_control(
    body: ActivateSafetyControlRequest,
    admin: User = Depends(get_current_admin),
    repo: RiskGateRepository = Depends(get_risk_gate_repository),
    paper_control_repo: PaperControlRepository = Depends(get_paper_control_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[SafetyControlView]:
    """78번 §4 "Only authorized operator ... routes may create scoped safety
    controls" 중 GLOBAL/TENANT/PROVIDER 범위 전용 경로 — 위 self-service
    엔드포인트와 분리해, 운영자 권한 없이는 라우팅 자체가 되지 않게 한다
    (RSK-006과 같은 원칙 — 권한 체크를 애플리케이션 로직에만 맡기지
    않는다)."""
    control = await activate_safety_control(
        repo,
        tenant_id=admin.user_id,
        actor_subject_id=admin.user_id,
        actor_is_admin=True,
        scope=SafetyScope(body.scope.value),
        scope_ref=body.scope_ref,
        reason=body.reason,
        audit_repo=audit_repo,
    )
    await apply_safety_control_to_deployments(
        paper_control_repo,
        scope=SafetyScope(control.scope.value),
        scope_ref=control.scope_ref,
        safety_control_id=control.id,
        actor_subject_id=admin.user_id,
        reason=body.reason,
    )
    return ok(control)


# R-23 — DRAFT→APPROVED→ACTIVE. `actor_is_risk_officer`는 `get_current_admin`
# 근사(activate_rule_bundle.py 모듈 docstring "미검증" 참조) — 전용 role이
# 생기기 전까지 운영자만 rule bundle을 승인·활성화할 수 있다.
@router.post("/rule-bundles/{bundle_id}:approve")
async def post_approve_rule_bundle(
    bundle_id: UUID,
    body: ApproveRuleBundleRequest,
    admin: User = Depends(get_current_admin),
    repo: RuleBundleRepository = Depends(get_rule_bundle_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[RiskRuleBundle]:
    bundle = await approve_rule_bundle(
        repo,
        audit_repo,
        bundle_id=bundle_id,
        approver_subject_id=admin.user_id,
        approval_ref=body.approval_ref,
        actor_is_risk_officer=True,
    )
    return ok(bundle)


@router.post("/rule-bundles/{bundle_id}:activate")
async def post_activate_rule_bundle(
    bundle_id: UUID,
    admin: User = Depends(get_current_admin),
    repo: RuleBundleRepository = Depends(get_rule_bundle_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[RiskRuleBundle]:
    bundle = await activate_rule_bundle(
        repo,
        audit_repo,
        bundle_id=bundle_id,
        actor_subject_id=admin.user_id,
        actor_is_risk_officer=True,
    )
    return ok(bundle)
