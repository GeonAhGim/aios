"""GrantMembership 커맨드.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§4.1 Membership,
§9 PLT-29. 저장소 접근은 PLT-27 `MembershipRepository`만 쓰고 새 SQL을 만들지
않는다 — 중복 활성 멤버십은 `insert_membership`의 부분 UNIQUE 위반을 그대로
전파한다(`ConcurrencyConflictError`, 이미 EXCEPTION_MAP에 등록됨).
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
    """73번 §4.1 GrantMembership guard "mfa_verified" — 최근 step-up 없이
    멤버십을 부여/재부여할 수 없다(403 AUTH_MFA_REQUIRED)."""


class GrantAuthorizationError(Exception):
    """73번 §4.1 전이표 — 신규 부여는 actor ACTIVE OWNER/ADMIN, REVOKED에서
    regrant는 actor OWNER만 가능하다. 위반 시 403 AUTHZ_FORBIDDEN."""


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
            # 이미 ACTIVE/SUSPENDED 멤버십이 있다 — insert_membership의 DB
            # 부분 UNIQUE는 ACTIVE만 걸러내므로(SUSPENDED는 통과), 여기서
            # 먼저 결정론적으로 막는다. ACTIVE 동시경합은 아래 insert가 그대로
            # 잡는다(같은 예외 타입으로 동형 처리).
            raise ConcurrencyConflictError(
                f"tenant_id={context.tenant_id} subject_id={subject_id}: 이미 "
                f"state={live[0].state.value} 멤버십이 있습니다."
            )

        is_regrant = len(history) > 0  # 전부 REVOKED뿐이면 regrant(73번 §4.1 4행)
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
