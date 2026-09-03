"""ResolveTenantContext 쿼리.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-28.

인증된 사용자 + 요청된 tenant(HTTP `X-Tenant-Id`, optional)로부터
`TenantContext`를 만든다. `contracts/v1.py`의 `TenantContext` docstring이
밝히는 P0 스콥(household/organization 멤버십 상태 머신은 아직 소비하지
않음)을 그대로 따른다 — tenant 미지정(또는 자기 자신)이면 personal
tenant(id == user_id, role OWNER 고정)로 바로 발급하고 membership repo를
조회하지 않는다. 다른 tenant를 명시했는데 그 tenant에 ACTIVE 멤버십이
없으면(PLT-27 `get_active_membership`이 없음/비활성 둘 다 `None`으로
동형 처리, §8.3) `TenantMismatchError` — 호출부(`foundation_deps.py`)가
403 `AUTH_TENANT_MISMATCH`로 번역한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.ports.membership_repository import MembershipRepository
from src.services.auth_service import User


class TenantMismatchError(Exception):
    """73번 §4 AUTH_TENANT_MISMATCH — 요청된 tenant에 사용자의 활성 멤버십이 없다."""


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
