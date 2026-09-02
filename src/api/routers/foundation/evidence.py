"""Audit Evidence 읽기 전용 API — 71번 §6 규칙."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.deps import get_current_admin, get_current_user
from src.api.foundation_deps import get_audit_event_repository
from src.api.schemas.foundation.evidence import AuditTimelinePage
from src.foundation.evidence.application.get_audit_timeline import get_audit_timeline
from src.foundation.evidence.application.verify_audit_chain import verify_audit_chain
from src.foundation.evidence.domain.rules import ChainIntegrityError
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


@router.post("/chain:verify")
async def post_verify_chain(
    tenant_id: UUID | None = Query(default=None),
    admin: User = Depends(get_current_admin),
    repo: AuditEventRepository = Depends(get_audit_event_repository),
) -> dict[str, bool]:
    """AUD-003 운영 도구 — 79번 §4. 관리자 전용(체인 전체 또는 특정
    tenant를 조회할 수 있어 일반 사용자에게는 열지 않는다). `tenant_id`를
    생략하면 system 이벤트(tenant_id IS NULL) 체인만 검증한다 — 전체
    tenant 순회는 이 리프의 스콥 밖(운영 배치 작업 대상)."""
    try:
        await verify_audit_chain(repo, tenant_id)
    except ChainIntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.detail) from exc
    return {"verified": True}
