"""Portfolio Mandate API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_auth_service, get_current_user, reauthenticate
from src.api.foundation_deps import (
    get_mandate_repository,
    get_risk_gate_repository,
    get_trust_repository,
)
from src.api.schemas.foundation.mandates import (
    ActivateRevisionRequest,
    MandateRevisionView,
    MandateRuleInput,
    MandateStatusResponse,
    PolicyDecisionView,
    PolicyEvaluationSubject,
)
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.mandates.application.activate_revision import (
    CoolingOffNotElapsedError,
    CrossTenantMandateAccessError,
    InvalidRevisionStateError,
    MaterialChangeRequiresFreshConsentError,
    MaterialChangeRequiresReauthError,
    RevisionNotFoundError,
)
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import (
    MandateAlreadyExistsError,
    create_draft_mandate,
)
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_policy_command
from src.foundation.mandates.application.pause_mandate import pause_mandate, resume_mandate
from src.foundation.mandates.application.propose_amendment import propose_amendment
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.mandates.projections import build_mandate_status_view
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.foundation.trust.ports.repository import TrustRepository
from src.services.auth_service import AuthService, User

router = APIRouter(prefix="/v1/foundation/mandates", tags=["foundation:mandates"])


@router.get("/status")
async def get_mandate_status(
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> MandateStatusResponse:
    view = await build_mandate_status_view(repo, user.user_id)
    return MandateStatusResponse(
        tenant_id=view.tenant_id,
        active_revision=view.active_revision,
        pending_revision=view.pending_revision,
    )


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def post_create_draft(
    body: MandateRuleInput,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> MandateRevisionView:
    try:
        return await create_draft_mandate(
            repo, tenant_id=user.user_id, subject_id=user.user_id, rules=body
        )
    except MandateAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/amendments", status_code=status.HTTP_201_CREATED)
async def post_propose_amendment(
    body: MandateRuleInput,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> MandateRevisionView:
    try:
        return await propose_amendment(repo, tenant_id=user.user_id, rules=body)
    except NoActiveMandateError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "활성 mandate가 없어 개정할 수 없습니다."
        ) from exc


@router.post("/revisions/{revision_id}:activate")
async def post_activate_revision(
    revision_id: UUID,
    body: ActivateRevisionRequest,
    user: User = Depends(get_current_user),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    trust_repo: TrustRepository = Depends(get_trust_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    auth: AuthService = Depends(get_auth_service),
) -> MandateRevisionView:
    reauthenticated = False
    if body.password is not None:
        await reauthenticate(auth, user, body.password, body.totp_code)
        reauthenticated = True

    try:
        result = await activate_revision_command(
            mandate_repo,
            trust_repo,
            tenant_id=user.user_id,
            subject_id=user.user_id,
            revision_id=revision_id,
            reauthenticated=reauthenticated,
        )
    except RevisionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 revision입니다.") from exc
    except CrossTenantMandateAccessError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 revision입니다.") from exc
    except InvalidRevisionStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MaterialChangeRequiresReauthError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"위험 상향 변경은 비밀번호(+MFA) 재인증이 필요합니다: {exc.reasons}",
        ) from exc
    except MaterialChangeRequiresFreshConsentError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"최신 동의가 필요합니다: {exc.reason_code}"
        ) from exc
    except CoolingOffNotElapsedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cooling-off이 {exc.remaining_seconds:.0f}초 남았습니다.",
        ) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    # 레드팀 지적(agent-platform-12) — evaluate_policy.py의 fingerprint 수정만으로
    # mandates 자신의 30초 캐시는 즉시 무효화되지만, risk_gate가 그 위에 얹은
    # 별도 10초 캐시(risk_evaluation)는 mandate 변경을 알 방법이 없어 그대로
    # stale ALLOW를 돌려줄 수 있다 — mandates 도메인이 risk_gate를 직접 알면
    # 안 되므로(71번 §4 방향성 위반), 이미 두 저장소를 다 아는 이 라우터가
    # orchestration만 담당한다(risk_gate.evaluate_risk_gate 라우터가 이미
    # mandates+connections+risk_gate 셋을 함께 의존하는 것과 동일한 패턴).
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return result


@router.post("/mandate:pause")
async def post_pause_mandate(
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> MandateRevisionView:
    try:
        result = await pause_mandate(repo, tenant_id=user.user_id)
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return result


@router.post("/mandate:resume")
async def post_resume_mandate(
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
) -> MandateRevisionView:
    try:
        result = await resume_mandate(repo, tenant_id=user.user_id)
    except ConcurrencyConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return result


@router.post("/policy:evaluate")
async def post_evaluate_policy(
    body: PolicyEvaluationSubject,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> PolicyDecisionView:
    try:
        return await evaluate_policy_command(repo, tenant_id=user.user_id, subject=body)
    except NoActiveMandateError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "활성 mandate가 없습니다."
        ) from exc
