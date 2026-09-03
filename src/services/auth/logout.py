"""PLT-24 — 세션 폐기(로그아웃) 유스케이스.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

`session_repository.revoke()`/`revoke_all_for_user()`(PLT-23)에 그대로
위임한다 — 둘 다 이미 멱등(`revoked_at IS NULL` 조건)이라 여기서는
사유 문자열과 소유권 검사만 더한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.services.auth import session_repository


class LogoutSessionMismatchError(Exception):
    """`session_id`가 요청한 `user_id` 소유가 아니다 — 다른 사용자의
    세션 id를 추측해 임의로 폐기시키지 못하게 막는다(403 `AUTHZ_FORBIDDEN`
    으로 매핑). 이미 폐기된 세션은 소유자를 알 수 없으므로 검사하지 않고
    조용히 no-op 처리한다(revoke()가 멱등이라 안전)."""


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
