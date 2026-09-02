"""GetAuditTimeline 쿼리 — 79번 §3 tenant-scoped 페이지 조회."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.evidence.application.append_audit_event import event_to_view
from src.foundation.evidence.contracts.v1 import AuditTimelinePage
from src.foundation.evidence.ports.repository import AuditEventRepository

MAX_PAGE_SIZE = 100
"""79번 §3 "maximum bounded page" — 호출자가 더 큰 값을 요청해도 이 값으로
잘라 무제한 조회를 막는다."""


async def get_audit_timeline(
    repo: AuditEventRepository,
    *,
    tenant_id: UUID,
    cursor: str | None = None,
    limit: int = 50,
    aggregate_type: str | None = None,
    action: str | None = None,
) -> AuditTimelinePage:
    bounded_limit = min(limit, MAX_PAGE_SIZE)
    events, next_cursor = await repo.list_timeline(
        tenant_id,
        cursor=cursor,
        limit=bounded_limit,
        aggregate_type=aggregate_type,
        action=action,
    )
    return AuditTimelinePage(
        items=[event_to_view(e) for e in events],
        next_cursor=next_cursor,
        as_of=datetime.now(timezone.utc),
    )
