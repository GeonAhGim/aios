"""SuspendMembership command.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. Session invalidation side-effect delegates to PLT-24 `logout_all`
(services/auth/logout.py) — never UPDATEs `auth_session` directly.
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


class SuspendTargetNotFoundError(Exception):
    """No ACTIVE membership for subject_id under context.tenant_id (includes
    ownership by another tenant — 73 §8.3 "404 isomorphism"). 404 RESOURCE_NOT_FOUND."""


class SuspendAuthorizationError(Exception):
    """73 §4.1 transition table — actor role is not authorised for ACTIVE->SUSPENDED.
    403 AUTHZ_FORBIDDEN."""


class SuspendLastOwnerError(Exception):
    """73 I4 "ACTIVE OWNER >= 1 per tenant". 409 STATE_INVALID_TRANSITION."""


async def suspend_membership(
    membership_repo: MembershipRepository,
    pool: asyncpg.Pool,
    context: TenantContext,
    *,
    subject_id: UUID,
) -> Membership:
    actor_role = MembershipRole(context.role)

    async with pool.acquire() as conn, conn.transaction():
        membership = await membership_repo.get_active_membership(
            conn, context.tenant_id, subject_id
        )
        if membership is None:
            raise SuspendTargetNotFoundError(
                f"tenant_id={context.tenant_id} subject_id={subject_id}: ACTIVE 멤버십이 "
                "없습니다."
            )
        if not is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=actor_role
        ):
            raise SuspendAuthorizationError(
                f"tenant_id={context.tenant_id}: role={actor_role.value}은(는) 멤버십을 "
                "정지할 권한이 없습니다."
            )

        # 73 §6-5 "same transaction" — FOR UPDATE-lock active OWNER rows
        # (count_active_owners) just before last-owner check. Race window:
        # between this predicate and the actual UPDATE, another transaction
        # may suspend a different OWNER in the same tenant.
        active_owners = await membership_repo.count_active_owners(conn, context.tenant_id)
        if would_remove_last_owner(
            active_owners, membership.role == MembershipRole.OWNER, MembershipState.SUSPENDED
        ):
            raise SuspendLastOwnerError(
                f"tenant_id={context.tenant_id}: 마지막 ACTIVE OWNER는 정지할 수 없습니다."
            )

        updated = await membership_repo.update_conditional_membership_state(
            conn,
            membership.id,
            context.tenant_id,
            expected_state=MembershipState.ACTIVE,
            expected_revision=membership.revision,
            new_state=MembershipState.SUSPENDED,
        )

    await logout_all(pool, user_id=subject_id)
    return updated
