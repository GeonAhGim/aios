"""17.3 — Notification history lookup.

Spec: 기능설계문서_v1.20.md#FD-17.3

Enables post-hoc proof of when approval requests were actually delivered — the
reliability of 4.9 forced-wait and dual-signature procedures depends on the fact
that "the notification actually reached the recipient."
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
    """Edge case (FD-17.3) — returns an empty list when no history exists
    for the period (not an error)."""
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
