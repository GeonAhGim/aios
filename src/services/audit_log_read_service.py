"""FD-7.2 (new read axis) — audit_log reads (AuditLogReadService).

Spec: 04_db_schema_v1.6.md (Audit Log, principle 8.10), src/core/logging/audit_log.py
(write-only, WORM)

Deviation: The spec never defines an audit log "read" endpoint, so there was no
way for operators to actually inspect audit history — principle 8.10 states
"it must be traceable who did what and when" for audit purposes, but recording
without a read path defeats that purpose. The WORM principle
(REVOKE UPDATE/DELETE FROM PUBLIC, see migration 9ec8a1ee28d7) does not block
reads — this service performs SELECT only.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

DEFAULT_PAGE_SIZE = 50


class AuditLogEntry(BaseModel):
    log_id: int
    user_id: UUID | None
    actor_agent: str
    action_type: str
    target_type: str | None
    target_id: str | None
    decision_data: dict[str, Any]
    verification_chain: dict[str, Any] | None
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    page_size: int


class AuditLogReadService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_entries(
        self,
        *,
        action_type: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> AuditLogPage:
        conditions: list[str] = []
        params: list[object] = []
        if action_type is not None:
            params.append(action_type)
            conditions.append(f"action_type = ${len(params)}")
        if target_type is not None:
            params.append(target_type)
            conditions.append(f"target_type = ${len(params)}")
        if target_id is not None:
            params.append(target_id)
            conditions.append(f"target_id = ${len(params)}")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM audit_log {where_clause}", *params
            )
            limit_param = len(params) + 1
            offset_param = len(params) + 2
            rows = await conn.fetch(
                f"""
                SELECT log_id, user_id, actor_agent, action_type, target_type, target_id,
                       decision_data, verification_chain, created_at
                FROM audit_log {where_clause}
                ORDER BY created_at DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
                """,
                *params,
                page_size,
                (page - 1) * page_size,
            )
        return AuditLogPage(
            items=[
                AuditLogEntry(
                    log_id=row["log_id"],
                    user_id=row["user_id"],
                    actor_agent=row["actor_agent"],
                    action_type=row["action_type"],
                    target_type=row["target_type"],
                    target_id=row["target_id"],
                    decision_data=json.loads(row["decision_data"]),
                    verification_chain=(
                        json.loads(row["verification_chain"])
                        if row["verification_chain"] is not None
                        else None
                    ),
                    created_at=row["created_at"],
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
