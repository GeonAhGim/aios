"""Compliance API — L4_compliance_and_regulatory_v1.0.md#9 CM-17: decision
query/explain, read-only mandate status. Per doc-71 §6, the router only
handles auth/DI/transport validation/command invocation — domain
exceptions are not caught here; `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` translates them into the envelope
at the global handler (same pattern as the task-1108 PLT-21 decision).

Mandate mutations (draft creation/amendment proposal/activation/
pause-resume) still come in through the single
`/v1/foundation/mandates` router (task-1108, CM-16 API contract) — exposing
the same commands again here would split the approval/re-evaluation
orchestration (e.g. the risk_gate cache invalidation after
`activate_revision`, see mandates.py) across two entry points. This router
adds the two capabilities that never got wired to an endpoint: CM-13's
`explain()` (decision query and explain collapse into one reproducible
response, satisfying both DoD items at once) and a read-only mandate
status view, so a single compliance screen no longer needs to straddle
two routers.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import get_mandate_repository
from src.api.schemas.foundation.compliance import ComplianceDecisionView, MandateStatusResponse
from src.foundation.mandates.application.explain import explain
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.mandates.projections import build_mandate_status_view
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/compliance", tags=["foundation:compliance"])


@router.get("/mandate/status")
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


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: UUID,
    user: User = Depends(get_current_user),
    repo: MandateRepository = Depends(get_mandate_repository),
) -> ApiResponse[ComplianceDecisionView]:
    result = await explain(repo, decision_id, tenant_id=user.user_id)
    return ok(result)
