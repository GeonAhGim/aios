"""Audit Evidence 읽기 전용 API — 71번 §6 규칙."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_current_user
from src.api.foundation_deps import get_audit_event_repository
from src.api.schemas.foundation.evidence import AuditTimelinePage
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
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
) -> AuditTimelinePage:
    return await get_audit_timeline(
        repo,
        tenant_id=user.user_id,
        cursor=cursor,
        limit=limit,
        aggregate_type=aggregate_type,
        action=action,
    )
