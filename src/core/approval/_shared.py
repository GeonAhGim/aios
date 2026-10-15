"""10.1 — ApprovalRequest model / shared lookup helper.

Spec: functional_design_v1.20.md#FD-10.1, ADR-2026-08-10-D

task-1723 P1-D: shared module from splitting service.py (303 lines, P6 > 300) —
service.py (creation/lookup) and resolution.py (approve/reject/cancel/expiry)
both import `ApprovalRequest`/`ApprovalError`/`_row_to_model`/`_fetch` here
(to avoid circular imports). Public path remains
`src.core.approval.service` — that module re-exports these symbols.

F2(task-12104): `approval_requests` now owns a real `tenant_id` column and
is RLS-ENABLE+FORCEd (`a4f27d91c6e8`). `_fetch` (used by approve/reject/
cancel/_update, all of which operate on a bare `request_id` with no tenant
context supplied by the caller — see service.py's module docstring on why
every exposed path today is either admin-only or already ownership-checked
one layer up) goes through `system_transaction` rather than
`tenant_transaction`: it needs to see rows across tenants by id, which is
exactly the `current_setting('app.role', true) = 'system'` carve-out
`b3c7f19ad2e6` already defined for `foundation_audit_event`, applied here
to the matching PLATFORM-scope NULL-`tenant_id` rows.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from src.core.db.tenant_scope import system_transaction


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
    async with system_transaction(pool) as conn:
        row = await conn.fetchrow("SELECT * FROM approval_requests WHERE id = $1", request_id)
    if row is None:
        raise ApprovalError(f"승인 요청을 찾을 수 없음: id={request_id}")
    return _row_to_model(row)


async def _resolve_tenant_id(pool: asyncpg.Pool, user_id: UUID) -> UUID:
    """F2(task-12104): the PM decision explicitly rejects treating `user_id`
    as a `tenant_id` proxy without verification -- this looks up the real
    `tenant` row (PERSONAL tenant's `id == user_id` invariant, PLT-26
    `f4a6b8c0d2e4`/`b8ac30eb4fe0`) instead of assuming it. `tenant` itself
    carries no RLS policy (it is the root of the hierarchy, not a
    tenant-owned leaf), so a bare `pool.acquire()` is correct here --
    there is no `app.tenant_id` to bind yet, that is exactly what this
    lookup produces for the caller to then open `tenant_transaction` with.
    Fails closed (raises, does not guess) if no such tenant row exists."""
    async with pool.acquire() as conn:
        tenant_id: UUID | None = await conn.fetchval("SELECT id FROM tenant WHERE id = $1", user_id)
    if tenant_id is None:
        raise ApprovalError(f"사용자 {user_id}에 대응하는 tenant를 찾을 수 없습니다.")
    return tenant_id
