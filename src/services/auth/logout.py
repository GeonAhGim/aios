"""PLT-24 — Session revocation (logout) use case.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

Delegates directly to `session_repository.revoke()` / `revoke_all_for_user()` (PLT-23) —
both are already idempotent (condition on `revoked_at IS NULL`), so this layer
adds only a reason string and ownership check.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.services.auth import session_repository


class LogoutSessionMismatchError(Exception):
    """The `session_id` does not belong to the requested `user_id` — prevents
    guessing and revoking other users' session IDs (mapped to 403
    `AUTHZ_FORBIDDEN`). Already-revoked sessions return owner information,
    so they are silently treated as a no-op (safe because revoke() is idempotent)."""


async def logout(pool: asyncpg.Pool, *, session_id: UUID, user_id: UUID) -> None:
    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, session_id)
        if session is not None and session.user_id != user_id:
            raise LogoutSessionMismatchError(
                f"session_id={session_id}는 user_id={user_id} 소유가 아닙니다"
            )
        await session_repository.revoke(conn, session_id, reason="logout")


async def logout_all(pool: asyncpg.Pool, *, user_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await session_repository.revoke_all_for_user(conn, user_id, reason="logout_all")
