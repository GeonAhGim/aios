"""RevokeMembership command.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. Session termination reuses PLT-24 `logout_all`, same as
`suspend_membership.py`.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import Membership, MembershipRole, MembershipState
from src.foundation.trust.domain.rules import (
    is_membership_transition_allowed,
    would_remove_last_owner,
)
from src.foundation.trust.ports.membership_repository import MembershipRepository
from src.services.auth.logout import logout_all


class RevokeTargetNotFoundError(Exception):
    """No ACTIVE/SUSPENDED membership for subject_id under context.tenant_id (including
    ownership by another tenant — 73 §8.3 "404 isomorphism"). 404 RESOURCE_NOT_FOUND."""


class RevokeAuthorizationError(Exception):
    """73 §4.1 transition table — actor role cannot perform this transition. 403 AUTHZ_FORBIDDEN."""


class RevokeLastOwnerError(Exception):
    """73 I4 "ACTIVE OWNER >= 1 per tenant". 409 STATE_INVALID_TRANSITION."""


async def revoke_membership(
    membership_repo: MembershipRepository,
    pool: asyncpg.Pool,
    context: TenantContext,
    *,
    subject_id: UUID,
) -> Membership:
    actor_role = MembershipRole(context.role)

    async with pool.acquire() as conn, conn.transaction():
        # get_active_membership returns only ACTIVE — but SUSPENDED is also a
        # revoke target (73 §4.1 "ACTIVE/SUSPENDED -> RevokeMembership"), so
        # find the non-REVOKED row in the history filtered by tenant. At most
        # one such row exists in the state machine.
        candidates = [
            m
            for m in await membership_repo.list_memberships_for_subject(conn, subject_id)
            if m.tenant_id == context.tenant_id and m.state != MembershipState.REVOKED
        ]
        membership = candidates[0] if candidates else None
        if membership is None:
            raise RevokeTargetNotFoundError(
                f"tenant_id={context.tenant_id} subject_id={subject_id}: 정지/활성 멤버십이 "
                "없습니다."
            )
        if not is_membership_transition_allowed(
            membership.state, MembershipState.REVOKED, actor_role=actor_role
        ):
            raise RevokeAuthorizationError(
                f"tenant_id={context.tenant_id}: role={actor_role.value}은(는) 멤버십을 "
                "철회할 권한이 없습니다."
            )

        active_owners = await membership_repo.count_active_owners(conn, context.tenant_id)
        if would_remove_last_owner(
            active_owners, membership.role == MembershipRole.OWNER, MembershipState.REVOKED
        ):
            raise RevokeLastOwnerError(
                f"tenant_id={context.tenant_id}: 마지막 ACTIVE OWNER는 철회할 수 없습니다."
            )

        updated = await membership_repo.update_conditional_membership_state(
            conn,
            membership.id,
            context.tenant_id,
            expected_state=membership.state,
            expected_revision=membership.revision,
            new_state=MembershipState.REVOKED,
        )

    await logout_all(pool, user_id=subject_id)
    return updated
