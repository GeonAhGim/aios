"""Trust Core membership admin API — 71번 §6 규칙: router는 auth/TenantContext
주입/transport validation/command invocation만 담당한다. `X-Tenant-Id`는 직접
읽지 않고 `get_tenant_context`(PLT-28)가 돌려준 컨텍스트만 신뢰한다 — cross-
tenant 시도는 그 의존성 단계에서 이미 403 `AUTH_TENANT_MISMATCH`로 막힌다.

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(§9 PLT-29 decision).
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
