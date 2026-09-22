"""PLT-35-fix(task-3850) -- break-glass grant request/approve HTTP endpoints.

QA(task-3794) defect I-10: `break_glass.request_grant`/`approve_grant` and
`admin_deps.get_current_mfa_admin`/`require_break_glass` existed in code but
were wired into no real route, so the DoD "admin route 403 on insufficient
MFA" could not be reproduced in production code (only direct function-call
tests existed).

This router opens the first two stages of the grant lifecycle (request/
approve) over HTTP -- `consume` gets no new endpoint; the
`require_break_glass(scope)` dependency consumes it directly on the
protected route (see admin_deps.py). Both requester and approver must
already hold an MFA_VERIFIED session, so `get_current_mfa_admin` is reused
as-is -- the `requester_auth_level`/`approver_auth_level` checks inside
`break_glass.py` are the second line of defense below that (still block a
caller that skips the router and calls the function directly)."""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status

from src.api.admin_deps import get_current_mfa_admin
from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import AuthenticatedUser, get_pool
from src.api.schemas.admin_break_glass import RequestBreakGlassGrantRequest
from src.core.security import break_glass
from src.core.security.break_glass import BreakGlassGrant
from src.foundation.trust.domain.rules.segregation_of_duty import (
    assert_actor_not_counterparty,
)

router = APIRouter(prefix="/admin/break-glass", tags=["admin:break-glass"])


@router.post("/grants", status_code=status.HTTP_201_CREATED)
async def post_request_grant(
    body: RequestBreakGlassGrantRequest,
    admin: AuthenticatedUser = Depends(get_current_mfa_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[BreakGlassGrant]:
    async with pool.acquire() as conn, conn.transaction():
        grant = await break_glass.request_grant(
            conn,
            requester_id=admin.user_id,
            requester_auth_level=admin.auth_level,
            scope=body.scope,
            reason=body.reason,
            ttl_minutes=body.ttl_minutes,
        )
    return ok(grant)


@router.post("/grants/{grant_id}:approve")
async def post_approve_grant(
    grant_id: UUID,
    admin: AuthenticatedUser = Depends(get_current_mfa_admin),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[BreakGlassGrant]:
    async with pool.acquire() as conn, conn.transaction():
        grant = await break_glass.approve_grant(
            conn,
            grant_id=grant_id,
            approver_id=admin.user_id,
            approver_auth_level=admin.auth_level,
            check_segregation_of_duty=assert_actor_not_counterparty,
        )
    return ok(grant)
