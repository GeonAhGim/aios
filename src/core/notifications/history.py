"""17.3 — 알림 이력 조회.

Spec: 기능설계문서_v1.20.md#FD-17.3

승인요청이 실제로 언제 전달됐는지 사후 증명 가능하게 한다 — 4.9 강제대기·
이중서명 절차의 신뢰성이 "알림이 실제로 갔다"는 사실에 의존한다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class NotificationHistoryEntry(BaseModel):
    event_type: str
    channel: str
    status: str
    created_at: datetime


async def list_notification_history(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    event_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[NotificationHistoryEntry]:
    """예외 상황(FD-17.3) — 해당 기간 이력이 없으면 빈 목록(오류 아님)."""
    conditions = ["user_id = $1"]
    params: list[object] = [user_id]

    if event_type is not None:
        params.append(event_type)
        conditions.append(f"event_type = ${len(params)}")
    if start is not None:
        params.append(start)
        conditions.append(f"created_at >= ${len(params)}")
    if end is not None:
        params.append(end)
        conditions.append(f"created_at <= ${len(params)}")

    query = (
        "SELECT event_type, channel, status, created_at FROM notifications "
        f"WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [NotificationHistoryEntry(**dict(row)) for row in rows]
