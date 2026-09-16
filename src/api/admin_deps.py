"""18번대 — 관리자 도구 서비스 팩토리 의존성.

L4 §2.2(B) PLT-35: `get_current_mfa_admin`/`require_break_glass`도 여기 둔다
(스펙 원문은 이 MFA 게이트를 기존 `get_current_admin`(실제 위치는
`src/api/deps.py:122`, 스펙 작성 시점의 가정과 달리 `admin_deps.py`가 아니다)
자체에 넣으라고 하지만, 그 함수는 `admin.py`·`evidence.py`·`ledger_admin.py`·
`risk_gate.py` 등 15개 이상의 기존 admin 라우트가 non-MFA 세션 토큰으로
이미 광범위하게 의존한다(그 라우트들의 통합테스트도 전부 MFA 없는
access_token을 쓴다) — 그 함수 자체를 바꾸면 이 리프의 DoD와 무관한 기존
라우트 전체가 한꺼번에 403으로 깨진다. 대신 `get_current_admin` 위에
MFA 게이트를 얹는 새 의존성을 두어, 이 리프가 실제로 요구하는 "MFA 미달
403"을 break-glass 소비 경로(및 향후 민감 커맨드)에 배선한다. 기존 15개
라우트를 MFA 필수로 전환하는 것은 PLT-2x류 라우터별 순차 이관과 동일한
패턴의 별도 PM 결정 사항이다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import asyncpg
from fastapi import Depends, Header

from src.core.security import break_glass
from src.core.security.break_glass import AdminMfaRequiredError, BreakGlassGrant, BreakGlassScope
from src.services.audit_log_read_service import AuditLogReadService
from src.services.dispute_resolution_service import DisputeResolutionService
from src.services.seller_suspension_service import SellerSuspensionService
from src.services.user_admin_service import UserAdminService
from src.services.verification_queue_service import VerificationQueueService

from .deps import AuthenticatedUser, get_current_admin, get_pool


def get_verification_queue_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> VerificationQueueService:
    return VerificationQueueService(pool)


def get_dispute_resolution_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> DisputeResolutionService:
    return DisputeResolutionService(pool)


def get_user_admin_service(pool: asyncpg.Pool = Depends(get_pool)) -> UserAdminService:
    return UserAdminService(pool)


def get_seller_suspension_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> SellerSuspensionService:
    return SellerSuspensionService(pool)


def get_audit_log_read_service(pool: asyncpg.Pool = Depends(get_pool)) -> AuditLogReadService:
    return AuditLogReadService(pool)


async def get_current_mfa_admin(
    admin: AuthenticatedUser = Depends(get_current_admin),
) -> AuthenticatedUser:
    if admin.auth_level != "MFA_VERIFIED":
        raise AdminMfaRequiredError(
            f"user_id={admin.user_id}: 이 관리자 작업은 MFA 재확인이 필요합니다."
        )
    return admin


def require_break_glass(scope: BreakGlassScope) -> Callable[..., Awaitable[BreakGlassGrant]]:
    """`X-Break-Glass-Grant` 헤더(grant id)를 1회 소비하는 의존성 팩토리.

    스코프가 요청한 것과 다르면(승인된 스코프가 아니면) 그 자리에서 거부한다
    — grant는 이미 `consume()`으로 소비된 뒤이므로, 잘못된 스코프로 같은
    grant를 다른 라우트에 다시 찔러볼 수는 없다(단발성 토큰은 첫 제시로 끝난다
    — 스코프가 틀렸다고 "안 쓴 것"으로 되돌려주지 않는다. fail-closed가
    재사용 편의보다 우선)."""

    async def _dependency(
        x_break_glass_grant: UUID = Header(..., alias="X-Break-Glass-Grant"),  # noqa: B008 -- UUID는 ruff의 면역 타입 목록 밖
        admin: AuthenticatedUser = Depends(get_current_mfa_admin),
        pool: asyncpg.Pool = Depends(get_pool),
    ) -> BreakGlassGrant:
        async with pool.acquire() as conn, conn.transaction():
            grant = await break_glass.consume(
                conn, grant_id=x_break_glass_grant, admin_id=admin.user_id
            )
        if grant.scope != scope:
            raise break_glass.BreakGlassInvalidStateError(
                f"grant_id={grant.id}: scope={grant.scope!r}는 {scope!r} 작업에 쓸 수 없습니다."
            )
        return grant

    return _dependency
