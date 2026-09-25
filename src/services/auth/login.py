"""PLT-24 — Login use case: authenticate + session creation + token pair issuance.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2, §3.4, §9 PLT-24.

Calls `AuthService.authenticate()` as-is (includes PLT-22 lockout atomicity
path and account enumeration prevention) — does not re-implement lock
determination or timing normalization logic here. `issue_token_pair()` is a
shared path that issues session + token for an already-authenticated user
(shortly after signup or after successful login), so `/auth/register`
reuses it unchanged.

Pre-PLT-26 (tenancy) scope — only personal tenants exist, so `tenant_id ==
user_id` is fixed (same convention as session_repository integration tests).
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
    """Create a session and issue a token pair for an already-authenticated user
    (shortly after signup or after a successful authenticate call). Session CRUD
    is delegated to `session_repository` (PLT-23); JWT issuance to
    `TokenIssuer` (PLT-23)."""
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
