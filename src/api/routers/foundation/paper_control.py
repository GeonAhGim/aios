"""Paper Execution & Control API — L4-71 §6 rule: router handles only auth/injection/transport
validation/command invocation.

L4-77 §4 "Control Center ... never calls a provider directly" — this router does not
call adapters directly (submit_paper_intent has no user-facing API yet
— reserved for future scheduler; see migration docstring).

Domain exceptions are not caught here — `src/api/contracts/exception_mapping.py`
`EXCEPTION_MAP` translates them to envelope in the global handler (§9 PLT-21b decision,
task-1217. start/resume (start_deployment.py) and pause/stop (pause_deployment.py) define
separate exception classes despite identical names (pre-audit duplicate, see CON-006 commit
docstring), so `EXCEPTION_MAP` registers both — registering only one leaves the other
half unmapped and returns 500 (actual bug observed; regression test:
test_foundation_paper_control_risk_gate_router.py::test_start_on_already_running_deployment_is_409_not_500).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import (
    get_connection_repository,
    get_mandate_repository,
    get_paper_control_repository,
    get_risk_gate_repository,
)
from src.api.schemas.foundation.paper_control import (
    DeploymentCommandRequest,
    DeploymentListResponse,
    PaperDeploymentView,
    RequestDeploymentRequest,
)
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.pause_deployment import (
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.application.request_deployment import request_deployment
from src.foundation.paper_control.application.start_deployment import (
    resume_deployment,
    start_deployment,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.paper_control.projections import build_deployment_list_view
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/paper-deployments", tags=["foundation:paper-control"])


@router.get("")
async def list_deployments(
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> ApiResponse[DeploymentListResponse]:
    view = await build_deployment_list_view(repo, user.user_id)
    return ok(DeploymentListResponse(deployments=view.deployments, as_of=view.as_of))


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_request_deployment(
    body: RequestDeploymentRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
) -> ApiResponse[PaperDeploymentView]:
    result = await request_deployment(
        repo,
        mandate_repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        package_ref=body.package_ref,
        connection_id=body.connection_id,
        adapter_type=body.adapter_type,
        provider_sandbox_account_ref=body.provider_sandbox_account_ref,
        endpoint_classification=body.endpoint_classification,
        idempotency_key=body.idempotency_key,
    )
    return ok(result)


@router.post("/{deployment_id}:start")
async def post_start_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    risk_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
) -> ApiResponse[PaperDeploymentView]:
    result = await start_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        deployment_id=deployment_id,
        idempotency_key=body.idempotency_key,
    )
    return ok(result)


@router.post("/{deployment_id}:resume")
async def post_resume_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    risk_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
) -> ApiResponse[PaperDeploymentView]:
    result = await resume_deployment(
        repo,
        risk_repo,
        mandate_repo,
        connection_repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        deployment_id=deployment_id,
        idempotency_key=body.idempotency_key,
    )
    return ok(result)


@router.post("/{deployment_id}:pause")
async def post_pause_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> ApiResponse[PaperDeploymentView]:
    result = await pause_deployment(
        repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        deployment_id=deployment_id,
        idempotency_key=body.idempotency_key,
    )
    return ok(result)


@router.post("/{deployment_id}:stop")
async def post_stop_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> ApiResponse[PaperDeploymentView]:
    result = await stop_deployment(
        repo,
        tenant_id=user.user_id,
        actor_subject_id=user.user_id,
        deployment_id=deployment_id,
        idempotency_key=body.idempotency_key,
    )
    return ok(result)
