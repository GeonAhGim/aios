"""ResolveTenantContext query.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-28.

Creates a `TenantContext` from an authenticated user and requested tenant
(HTTP `X-Tenant-Id`, optional). Follows the P0 scope declared by the
`TenantContext` docstring in `contracts/v1.py` (household/organization
membership state machine not yet consumed). If tenant is unspecified or
self-referential, immediately issues a personal tenant (id == user_id,
role OWNER fixed) without querying the membership repo. If an explicit
tenant is requested but has no ACTIVE membership (PLT-27
`get_active_membership` returns None for both missing and inactive;
§8.3 treats them uniformly), raises `TenantMismatchError` — the caller
in `foundation_deps.py` translates this to 403 `AUTH_TENANT_MISMATCH`.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.ports.membership_repository import MembershipRepository
from src.services.auth_service import User


class TenantMismatchError(Exception):
    """Error #73 §4 AUTH_TENANT_MISMATCH — the requested tenant has no
    active membership for the user."""


async def resolve_tenant_context(
    repo: MembershipRepository,
    conn: asyncpg.Connection,
    *,
    user: User,
    requested_tenant_id: UUID | None,
    mfa_verified: bool,
) -> TenantContext:
    if requested_tenant_id is None or requested_tenant_id == user.user_id:
        return TenantContext(
            tenant_id=user.user_id,
            subject_id=user.user_id,
            role="OWNER",
            mfa_verified=mfa_verified,
        )

    membership = await repo.get_active_membership(conn, requested_tenant_id, user.user_id)
    if membership is None:
        raise TenantMismatchError(
            f"user_id={user.user_id}: tenant_id={requested_tenant_id}에 활성 멤버십이 없습니다."
        )
    return TenantContext(
        tenant_id=membership.tenant_id,
        subject_id=user.user_id,
        role=membership.role.value,
        mfa_verified=mfa_verified,
        membership_id=membership.id,
    )
