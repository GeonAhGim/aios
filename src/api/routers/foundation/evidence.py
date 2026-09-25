"""Read-only Audit Evidence API — Rule 71 §6.

Domain exceptions are not caught here — `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` wraps and translates them via the
global handler (§9 PLT-21 decision, task-1108).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_admin, get_current_user
from src.api.foundation_deps import get_audit_event_repository
from src.api.schemas.foundation.evidence import AuditTimelinePage
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.services.auth_service import User

router = APIRouter(prefix="/v1/foundation/evidence", tags=["foundation:evidence"])


@router.get("/timeline")
async def get_timeline(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    aggregate_type: str | None = None,
    action: str | None = None,
    user: User = Depends(get_current_user),
    repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[AuditTimelinePage]:
    page = await get_audit_timeline(
        repo,
        tenant_id=user.user_id,
        cursor=cursor,
        limit=limit,
        aggregate_type=aggregate_type,
        action=action,
    )
    return ok(page)


@router.post("/chain:verify")
async def post_verify_chain(
    tenant_id: UUID | None = Query(default=None),
    admin: User = Depends(get_current_admin),
    repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> ApiResponse[dict[str, bool]]:
    """AUD-003 operational tool — Rule 79 §4. Admin-only (not exposed to
    regular users since it can query the full chain or a specific tenant).
    Omitting `tenant_id` verifies only the system event chain (tenant_id IS
    NULL) — iterating all tenants is out of scope for this leaf (operational
    batch task target)."""
    await verify_audit_chain(repo, tenant_id)
    return ok({"verified": True})
