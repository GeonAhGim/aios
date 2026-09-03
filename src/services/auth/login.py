"""PLT-24 — 로그인 유스케이스: authenticate + 세션 생성 + 토큰 쌍 발급.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2, §3.4, §9 PLT-24.

`AuthService.authenticate()`(PLT-22 lockout 원자화 경로 포함, 계정열거
방지 포함)를 그대로 호출한다 — 잠금 판정·타이밍 정규화 로직을 여기서
재구현하지 않는다. `issue_token_pair()`는 이미 인증된 user(가입 직후
또는 로그인 성공)에 세션+토큰을 발급하는 공용 경로라 `/auth/register`도
그대로 재사용한다.

PLT-26(테넌시) 이전 스콥 — 개인 테넌트만 존재하므로 `tenant_id ==
user_id` 고정이다(session_repository 통합테스트와 동일 관례).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import asyncpg

from src.services.auth import session_repository
from src.services.auth.tokens import (
    ACCESS_TTL_MINUTES,
    REFRESH_TTL_DAYS,
    AuthLevel,
    ClientInfo,
    TokenIssuer,
    TokenPairResponse,
)
from src.services.auth_service import AuthService, User


def _hash_client_field(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _auth_level_for(user: User) -> AuthLevel:
    return "MFA_VERIFIED" if user.mfa_verified_at is not None else "PASSWORD"


async def issue_token_pair(
    pool: asyncpg.Pool,
    issuer: TokenIssuer,
    user: User,
    *,
    client: ClientInfo | None = None,
) -> TokenPairResponse:
    """이미 인증된(가입 직후 또는 authenticate 성공) user에 세션을 새로
    만들고 토큰 쌍을 발급한다. 세션 CRUD는 `session_repository`(PLT-23)에,
    JWT 발급은 `TokenIssuer`(PLT-23)에 그대로 위임한다."""
    client = client or ClientInfo()
    refresh_plain, refresh_hash = TokenIssuer.issue_refresh()
    auth_level = _auth_level_for(user)
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS)

    async with pool.acquire() as conn:
        session = await session_repository.insert_session(
            conn,
            user_id=user.user_id,
            tenant_id=user.user_id,
            refresh_hash=refresh_hash,
            ip_hash=_hash_client_field(client.ip),
            ua_hash=_hash_client_field(client.user_agent),
            expires_at=expires_at,
            auth_level=auth_level,
        )

    access_token = issuer.issue_access(
        user_id=user.user_id,
        tenant_id=session.tenant_id,
        session_id=session.id,
        auth_level=auth_level,
    )
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_plain,
        expires_in=ACCESS_TTL_MINUTES * 60,
        session_id=session.id,
    )


async def login(
    pool: asyncpg.Pool,
    auth: AuthService,
    issuer: TokenIssuer,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
    client: ClientInfo | None = None,
) -> TokenPairResponse:
    user = await auth.authenticate(email, password, totp_code=totp_code)
    return await issue_token_pair(pool, issuer, user, client=client)
