"""Trust Core membership admin API — Rule §6 (71): the router is responsible
only for auth/TenantContext injection, transport validation, and command
invocation. It does not read `X-Tenant-Id` directly; it trusts only the
context returned by `get_tenant_context` (PLT-28) — cross-tenant attempts
are already blocked with 403 `AUTH_TENANT_MISMATCH` at that dependency
stage.

Domain exceptions are not caught here — the `EXCEPTION_MAP` in
`src/api/contracts/exception_mapping.py` translates them in the global
handler (§9 PLT-29 decision).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import get_membership_repository, get_tenant_context
from src.foundation.trust.application.grant_membership import grant_membership
from src.foundation.trust.application.revoke_membership import revoke_membership
from src.foundation.trust.application.suspend_membership import suspend_membership
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import Membership, MembershipRole, MembershipState
from src.foundation.trust.ports.membership_repository import MembershipRepository

router = APIRouter(prefix="/v1/foundation/trust", tags=["foundation:trust-memberships"])


class GrantMembershipRequest(BaseModel):
    subject_id: UUID
    role: MembershipRole


class MembershipResponse(BaseModel):
    membership_id: UUID
    tenant_id: UUID
    subject_id: UUID
    role: MembershipRole
    state: MembershipState
    revision: int


def _to_response(membership: Membership) -> MembershipResponse:
    return MembershipResponse(
        membership_id=membership.id,
        tenant_id=membership.tenant_id,
        subject_id=membership.subject_id,
        role=membership.role,
        state=membership.state,
        revision=membership.revision,
    )


@router.post("/memberships", status_code=status.HTTP_201_CREATED)
async def post_grant_membership(
    body: GrantMembershipRequest,
    context: TenantContext = Depends(get_tenant_context),
    membership_repo: MembershipRepository = Depends(get_membership_repository),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[MembershipResponse]:
    membership = await grant_membership(
        membership_repo, pool, context, subject_id=body.subject_id, role=body.role
    )
    return ok(_to_response(membership))


@router.post("/memberships/{subject_id}:suspend")
async def post_suspend_membership(
    subject_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    membership_repo: MembershipRepository = Depends(get_membership_repository),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[MembershipResponse]:
    membership = await suspend_membership(membership_repo, pool, context, subject_id=subject_id)
    return ok(_to_response(membership))


@router.post("/memberships/{subject_id}:revoke")
async def post_revoke_membership(
    subject_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    membership_repo: MembershipRepository = Depends(get_membership_repository),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[MembershipResponse]:
    membership = await revoke_membership(membership_repo, pool, context, subject_id=subject_id)
    return ok(_to_response(membership))
