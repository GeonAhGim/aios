"""10.1 — ApprovalRequest model / shared lookup helper.

Spec: functional_design_v1.20.md#FD-10.1, ADR-2026-08-10-D

task-1723 P1-D: shared module from splitting service.py (303 lines, P6 > 300) —
service.py (creation/lookup) and resolution.py (approve/reject/cancel/expiry)
both import `ApprovalRequest`/`ApprovalError`/`_row_to_model`/`_fetch` here
(to avoid circular imports). Public path remains
`src.core.approval.service` — that module re-exports these symbols.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel


class ApprovalError(Exception):
    """Business rule violation raised by this module — caller may surface the reason to the user."""


class ApprovalRequest(BaseModel):
    id: int
    scope: str
    user_id: UUID | None
    trigger_source: str
    provenance: str | None
    context: dict[str, Any]
    requested_action: str
    approval_mode: str
    status: str
    mandatory_wait_seconds: int
    first_approver_id: UUID | None
    second_approver_id: UUID | None
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


def _row_to_model(row: asyncpg.Record) -> ApprovalRequest:
    data = dict(row)
    data["context"] = json.loads(data["context"])
    return ApprovalRequest(**data)


async def _fetch(pool: asyncpg.Pool, request_id: int) -> ApprovalRequest:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM approval_requests WHERE id = $1", request_id)
    if row is None:
        raise ApprovalError(f"승인 요청을 찾을 수 없음: id={request_id}")
    return _row_to_model(row)
