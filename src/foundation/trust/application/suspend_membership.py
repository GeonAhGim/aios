"""SuspendMembership 커맨드.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. 세션 폐기 부작용은 PLT-24 `logout_all`(services/auth/logout.py)을
그대로 호출한다 — `auth_session`을 직접 UPDATE하지 않는다.
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
    """context.tenant_id에 subject_id의 ACTIVE 멤버십이 없다(다른 tenant 소유
    포함 — 73번 §8.3 "404 동형"). 404 RESOURCE_NOT_FOUND."""


class SuspendAuthorizationError(Exception):
    """73번 §4.1 전이표 — actor role이 ACTIVE->SUSPENDED 전이를 수행할 수 없다.
    403 AUTHZ_FORBIDDEN."""


class SuspendLastOwnerError(Exception):
    """73번 I4 "tenant당 ACTIVE OWNER >= 1". 409 STATE_INVALID_TRANSITION."""


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

        # 73번 §6-5 "same transaction" — last-owner 판정 직전 활성 OWNER 행을
        # FOR UPDATE로 잠근다(count_active_owners). 이 트랜잭션 밖에서 세면
        # 판정과 실제 UPDATE 사이에 경합이 생길 수 있다.
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
