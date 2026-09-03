"""RevokeMembership 커맨드.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. `suspend_membership.py`와 동일하게 세션 폐기는 PLT-24 `logout_all`을
재사용한다.
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
    """context.tenant_id에 subject_id의 ACTIVE/SUSPENDED 멤버십이 없다(다른
    tenant 소유 포함 — 73번 §8.3 "404 동형"). 404 RESOURCE_NOT_FOUND."""


class RevokeAuthorizationError(Exception):
    """73번 §4.1 전이표 — actor role이 이 전이를 수행할 수 없다. 403 AUTHZ_FORBIDDEN."""


class RevokeLastOwnerError(Exception):
    """73번 I4 "tenant당 ACTIVE OWNER >= 1". 409 STATE_INVALID_TRANSITION."""


async def revoke_membership(
    membership_repo: MembershipRepository,
    pool: asyncpg.Pool,
    context: TenantContext,
    *,
    subject_id: UUID,
) -> Membership:
    actor_role = MembershipRole(context.role)

    async with pool.acquire() as conn, conn.transaction():
        # get_active_membership은 ACTIVE만 돌려준다 — SUSPENDED도 revoke 대상
        # 이므로(73번 §4.1 "ACTIVE/SUSPENDED -> RevokeMembership"), tenant로
        # 필터링한 이력에서 아직 REVOKED되지 않은 행을 찾는다. 상태 머신상
        # 이런 행은 최대 1개다.
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
