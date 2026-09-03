"""PLT-24 — refresh 회전 유스케이스.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

클라이언트는 로그인 응답의 `session_id`를 `refresh_token`과 함께
보관했다가 갱신 시 그대로 되돌려준다 — `refresh_hash`만으로는 어느
세션이 회전 대상인지 알 수 없다(옛 해시는 회전 후 DB 어디에도 남지
않으므로 역참조가 불가능하다). 회전 자체는
`session_repository.rotate_refresh()`(PLT-23, 105 조건부 UPDATE)에
그대로 위임한다 — `expected_hash`가 현재 DB 값과 다르면(이미 회전된
옛 토큰 재사용이든, 틀린 토큰 추측이든) 그 함수가 이미 세션을 revoke하고
`RefreshReuseDetected`를 던지므로 여기서는 그대로 전파하기만 한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.services.auth import session_repository
from src.services.auth.tokens import (
    ACCESS_TTL_MINUTES,
    TokenIssuer,
    TokenPairResponse,
    hash_refresh_token,
)


class RefreshSessionNotFoundError(Exception):
    """`session_id`가 존재하지 않거나 이미 revoke됐다. 회전 대상 자체가
    없어 `session_repository.rotate_refresh()`를 호출하지 않으므로
    `RefreshReuseDetected`와는 별도 예외로 구분한다(둘 다 401
    `AUTH_SESSION_REVOKED`로 매핑되지만 원인이 다르다)."""


class RefreshTokenExpiredError(Exception):
    """세션은 활성이지만 14일 절대 만료(`expires_at`)를 지났다 — 재사용
    감지(탈취 의심)와 구분해 401 `AUTH_TOKEN_EXPIRED`로 매핑한다(재로그인
    유도, 세션 탈취 의심 알림 대상 아님)."""


async def refresh(
    pool: asyncpg.Pool,
    issuer: TokenIssuer,
    *,
    session_id: UUID,
    refresh_token: str,
) -> TokenPairResponse:
    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, session_id)
        if session is None:
            raise RefreshSessionNotFoundError(f"session_id={session_id}: 활성 세션이 없습니다")

        now = datetime.now(timezone.utc)
        if session.expires_at <= now:
            await session_repository.revoke(conn, session_id, reason="expired")
            raise RefreshTokenExpiredError(f"session_id={session_id}: refresh 만료")

        new_plaintext, new_hash = TokenIssuer.issue_refresh()
        rotated = await session_repository.rotate_refresh(
            conn,
            session_id,
            expected_hash=hash_refresh_token(refresh_token),
            new_hash=new_hash,
        )

    access_token = issuer.issue_access(
        user_id=rotated.user_id,
        tenant_id=rotated.tenant_id,
        session_id=rotated.id,
        auth_level=rotated.auth_level,
    )
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=new_plaintext,
        expires_in=ACCESS_TTL_MINUTES * 60,
        session_id=rotated.id,
    )
