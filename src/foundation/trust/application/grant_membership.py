"""GrantMembership command.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. Repository access uses only PLT-27 `MembershipRepository`; do not
create new SQL — duplicate active membership propagates the partial UNIQUE
violation from `insert_membership` as-is
(`ConcurrencyConflictError`, already registered in EXCEPTION_MAP).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import Membership, MembershipRole, MembershipState
from src.foundation.trust.domain.rules import is_membership_transition_allowed, role_can
from src.foundation.trust.ports.membership_repository import MembershipRepository


class MembershipMfaRequiredError(Exception):
    """§4.1 GrantMembership guard "mfa_verified" — without a recent step-up,
    membership cannot be granted or re-granted (403 AUTH_MFA_REQUIRED)."""


class GrantAuthorizationError(Exception):
    """§4.1 transition table — initial grant requires actor ACTIVE OWNER/ADMIN;
    regrant requires actor OWNER only. Violation returns 403 AUTHZ_FORBIDDEN."""


async def grant_membership(
    membership_repo: MembershipRepository,
    pool: asyncpg.Pool,
    context: TenantContext,
    *,
    subject_id: UUID,
    role: MembershipRole,
) -> Membership:
    if not context.mfa_verified:
        raise MembershipMfaRequiredError(
            f"tenant_id={context.tenant_id}: 멤버십 부여는 MFA 재확인이 필요합니다."
        )
    actor_role = MembershipRole(context.role)

    async with pool.acquire() as conn, conn.transaction():
        history = [
            m
            for m in await membership_repo.list_memberships_for_subject(conn, subject_id)
            if m.tenant_id == context.tenant_id
        ]
        live = [m for m in history if m.state != MembershipState.REVOKED]
        if live:
            # An ACTIVE/SUSPENDED membership already exists — the DB partial UNIQUE
            # on `insert_membership` filters only ACTIVE (SUSPENDED passes), so we
            # deterministically block it here first. Concurrent ACTIVE collision is
            # caught by the insert below (same exception type).
            raise ConcurrencyConflictError(
                f"tenant_id={context.tenant_id} subject_id={subject_id}: 이미 "
                f"state={live[0].state.value} 멤버십이 있습니다."
            )

        is_regrant = len(history) > 0  # regrant when all are REVOKED (§4.1 line 4)
        if is_regrant:
            allowed = is_membership_transition_allowed(
                MembershipState.REVOKED, MembershipState.ACTIVE, actor_role=actor_role
            )
        else:
            allowed = role_can(actor_role, "admin")
        if not allowed:
            raise GrantAuthorizationError(
                f"tenant_id={context.tenant_id}: role={actor_role.value}은(는) 멤버십을 "
                "부여할 권한이 없습니다."
            )

        return await membership_repo.insert_membership(
            conn,
            tenant_id=context.tenant_id,
            subject_id=subject_id,
            role=role,
            created_by=context.subject_id,
        )
