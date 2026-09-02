"""Paper Execution & Control API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

77번 §4 "Control Center ... never calls a provider directly" — 이 라우터는
adapter를 직접 부르지 않는다(submit_paper_intent는 아직 사용자 트리거
API가 없다 — 미래 스케줄러 전용, 마이그레이션 docstring 참조)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

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
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.application.pause_deployment import (
    CrossTenantDeploymentAccessError,
    DeploymentNotFoundError,
    pause_deployment,
    stop_deployment,
)
from src.foundation.paper_control.application.pause_deployment import (
    InvalidDeploymentStateError as PauseInvalidDeploymentStateError,
)
from src.foundation.paper_control.application.request_deployment import (
    IdempotencyKeyConflictError,
    NoActiveMandateError,
    request_deployment,
)
from src.foundation.paper_control.application.start_deployment import (
    InvalidDeploymentStateError as StartInvalidDeploymentStateError,
)
from src.foundation.paper_control.application.start_deployment import (
    RiskGateDeniedError,
    resume_deployment,
    start_deployment,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.paper_control.projections import build_deployment_list_view
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.auth_service import User

# start/resume(start_deployment.py)와 pause/stop(pause_deployment.py)이
# 이름은 같지만 서로 다른 InvalidDeploymentStateError 클래스를 각자 정의해뒀다
# (전수감사 이전부터 있던 중복 — 리뷰 중 발견, CON-006 커밋 docstring 참조).
# 새 공용 클래스로 합치는 대신(두 애플리케이션 모듈의 기존 계약을 건드리지
# 않기 위해) 라우터에서 둘 다 잡는다 — 하나만 잡으면 나머지 절반이 매핑되지
# 않은 채 500으로 샌다(실제로 start/resume 쪽이 그랬다).
_InvalidDeploymentStateErrors = (PauseInvalidDeploymentStateError, StartInvalidDeploymentStateError)

router = APIRouter(prefix="/v1/foundation/paper-deployments", tags=["foundation:paper-control"])


@router.get("")
async def list_deployments(
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> DeploymentListResponse:
    view = await build_deployment_list_view(repo, user.user_id)
    return DeploymentListResponse(deployments=view.deployments, as_of=view.as_of)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_request_deployment(
    body: RequestDeploymentRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
) -> PaperDeploymentView:
    try:
        return await request_deployment(
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
    except NoActiveMandateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "활성 mandate가 없습니다.") from exc
    except InvalidProvenanceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except IdempotencyKeyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{deployment_id}:start")
async def post_start_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    risk_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
) -> PaperDeploymentView:
    try:
        return await start_deployment(
            repo,
            risk_repo,
            mandate_repo,
            connection_repo,
            tenant_id=user.user_id,
            actor_subject_id=user.user_id,
            deployment_id=deployment_id,
            idempotency_key=body.idempotency_key,
        )
    except (DeploymentNotFoundError, CrossTenantDeploymentAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 배포입니다.") from exc
    except _InvalidDeploymentStateErrors as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RiskGateDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{deployment_id}:resume")
async def post_resume_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
    risk_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
) -> PaperDeploymentView:
    try:
        return await resume_deployment(
            repo,
            risk_repo,
            mandate_repo,
            connection_repo,
            tenant_id=user.user_id,
            actor_subject_id=user.user_id,
            deployment_id=deployment_id,
            idempotency_key=body.idempotency_key,
        )
    except (DeploymentNotFoundError, CrossTenantDeploymentAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 배포입니다.") from exc
    except _InvalidDeploymentStateErrors as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RiskGateDeniedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{deployment_id}:pause")
async def post_pause_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> PaperDeploymentView:
    try:
        return await pause_deployment(
            repo,
            tenant_id=user.user_id,
            actor_subject_id=user.user_id,
            deployment_id=deployment_id,
            idempotency_key=body.idempotency_key,
        )
    except (DeploymentNotFoundError, CrossTenantDeploymentAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 배포입니다.") from exc
    except _InvalidDeploymentStateErrors as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{deployment_id}:stop")
async def post_stop_deployment(
    deployment_id: UUID,
    body: DeploymentCommandRequest,
    user: User = Depends(get_current_user),
    repo: PaperControlRepository = Depends(get_paper_control_repository),
) -> PaperDeploymentView:
    try:
        return await stop_deployment(
            repo,
            tenant_id=user.user_id,
            actor_subject_id=user.user_id,
            deployment_id=deployment_id,
            idempotency_key=body.idempotency_key,
        )
    except (DeploymentNotFoundError, CrossTenantDeploymentAccessError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 배포입니다.") from exc
    except _InvalidDeploymentStateErrors as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
