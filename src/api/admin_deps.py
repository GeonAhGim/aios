"""18번대 — 관리자 도구 서비스 팩토리 의존성.

L4 Sec 2.2(B) PLT-35: `get_current_mfa_admin`/`require_break_glass` also live
here. The spec text says to put this MFA gate directly on the existing
`get_current_admin` (actually located at `src/api/deps.py:122`, not
`admin_deps.py` as the spec's authors assumed) -- but that function is
already depended on broadly, with non-MFA session tokens, by 15+ existing
admin routes (`admin.py`, `evidence.py`, `ledger_admin.py`, `risk_gate.py`,
...; their integration tests all use access_tokens with no MFA step-up).
Changing that function directly would break every one of those routes with
403s, none of which are in this leaf's DoD. Instead, a new dependency layers
the MFA gate on top of `get_current_admin`, wiring the "MFA not satisfied ->
403" behavior this leaf actually requires into the break-glass consume path
(and future sensitive commands). Converting the existing 15 routes to require
MFA is a separate PM decision, the same pattern as the PLT-2x per-router
migration series."""

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
    """Dependency factory that consumes the `X-Break-Glass-Grant` header
    (a grant id) exactly once.

    If the scope doesn't match what was requested (i.e. doesn't match the
    approved scope), this rejects on the spot -- the grant has already been
    consumed by `consume()` by then, so it can't be probed against a
    different route with the wrong scope (a single-use token ends at its
    first presentation -- a scope mismatch does not "give it back" as
    unused. fail-closed wins over reuse convenience)."""

    async def _dependency(
        x_break_glass_grant: UUID = Header(..., alias="X-Break-Glass-Grant"),  # noqa: B008 -- UUID isn't in ruff's immune-type list
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
