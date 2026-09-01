"""FD-7.2(신설 읽기 축) — audit_log 조회 (AuditLogReadService).

Spec: 04_db_schema_v1.6.md(Audit Log, 8.10 원칙), src/core/logging/audit_log.py
(기록 전용, WORM)

편차: 스펙 어디에도 감사로그 "조회" 엔드포인트가 명시되지 않아 관리자가
실제로 감사 이력을 확인할 방법이 없었다 — 8.10 원칙 자체가 "누가 언제
무엇을 했는지 추적 가능해야 한다"는 감사 목적인데, 기록만 하고 조회
경로가 없으면 그 목적을 달성하지 못한다. WORM 원칙(REVOKE UPDATE/DELETE
FROM PUBLIC, 마이그레이션 9ec8a1ee28d7 참조)은 읽기를 막지 않는다 —
이 서비스는 SELECT만 수행한다.
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
