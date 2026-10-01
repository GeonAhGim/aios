"""Portfolio Mandate API — §6: router handles auth, injection, transport
validation, and command invocation only.

Domain exceptions are not caught here — ``src/api/contracts/exception_mapping.py``
``EXCEPTION_MAP`` translates them via the global handler as an envelope
(§9 PLT-21 decision, task-1108).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_auth_service, get_current_user, reauthenticate
from src.api.foundation_deps import (
    get_audit_event_repository,
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
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.mandates.application.activate_revision import (
    activate_revision as activate_revision_command,
)
from src.foundation.mandates.application.create_draft_mandate import create_draft_mandate
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
) -> ApiResponse[MandateStatusResponse]:
    view = await build_mandate_status_view(repo, user.user_id)
    return ok(
        MandateStatusResponse(
            tenant_id=view.tenant_id,
            active_revision=view.active_revision,
            pending_revision=view.pending_revision,
        )
    )


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def post_create_draft(
    body: MandateRuleInput,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> ApiResponse[MandateRevisionView]:
    result = await create_draft_mandate(
        repo, tenant_id=user.user_id, subject_id=user.user_id, rules=body
    )
    return ok(result)


@router.post("/amendments", status_code=status.HTTP_201_CREATED)
async def post_propose_amendment(
    body: MandateRuleInput,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> ApiResponse[MandateRevisionView]:
    # CM-5(task-2118) leaves `proposer_id`/`audit_repo` unset here: this
    # single-actor MVP endpoint has one tenant member propose and activate
    # their own mandate (same `user.user_id` for both), so wiring the new
    # author != approver enforcement to this route would block the only
    # activation path that exists today. The command itself already
    # enforces CM-A3 whenever a caller supplies both — wiring this route to
    # a real second approver is a separate, later leaf (multi-member
    # mandate approval).
    result = await propose_amendment(repo, tenant_id=user.user_id, rules=body)
    return ok(result)


@router.post("/revisions/{revision_id}:activate")
async def post_activate_revision(
    revision_id: UUID,
    body: ActivateRevisionRequest,
    user: User = Depends(get_current_user),
    mandate_repo: MandateRepository = Depends(get_mandate_repository),
    trust_repo: TrustRepository = Depends(get_trust_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    auth: AuthService = Depends(get_auth_service),
) -> ApiResponse[MandateRevisionView]:
    reauthenticated = False
    if body.password is not None:
        await reauthenticate(auth, user, body.password, body.totp_code)
        reauthenticated = True

    result = await activate_revision_command(
        mandate_repo,
        trust_repo,
        tenant_id=user.user_id,
        subject_id=user.user_id,
        revision_id=revision_id,
        reauthenticated=reauthenticated,
        audit_repo=audit_repo,
    )

    # Red-team feedback (agent-platform-12) — modifying the fingerprint in
    # evaluate_policy.py immediately invalidates mandates' own 30-second cache,
    # but the risk_gate's separate 10-second cache (risk_evaluation) has no way
    # to learn about mandate changes and may return a stale ALLOW — since the
    # mandates domain must not know about risk_gate directly (§4 directionality
    # rule), this router which already knows both repositories handles only the
    # orchestration (same pattern as risk_gate.evaluate_risk_gate router which
    # already depends on mandates+connections+risk_gate together).
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return ok(result)


@router.post("/mandate:pause")
async def post_pause_mandate(
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[MandateRevisionView]:
    result = await pause_mandate(repo, tenant_id=user.user_id, audit_repo=audit_repo)
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return ok(result)


@router.post("/mandate:resume")
async def post_resume_mandate(
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
    risk_gate_repo: RiskGateRepository = Depends(get_risk_gate_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[MandateRevisionView]:
    result = await resume_mandate(repo, tenant_id=user.user_id, audit_repo=audit_repo)
    await risk_gate_repo.invalidate_evaluations(tenant_id=user.user_id)
    return ok(result)


@router.post("/policy:evaluate")
async def post_evaluate_policy(
    body: PolicyEvaluationSubject,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> ApiResponse[PolicyDecisionView]:
    result = await evaluate_policy_command(repo, tenant_id=user.user_id, subject=body)
    return ok(result)
