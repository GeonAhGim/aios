"""PLT-24 — refresh rotation use case.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

The client stores the `session_id` from the login response alongside the
`refresh_token` and returns it unchanged on refresh — the `refresh_hash`
alone cannot identify which session is the rotation target (the old hash
leaves no trace in the DB after rotation, making reverse lookup impossible).
Rotation itself is delegated to
`session_repository.rotate_refresh()` (PLT-23, 105 conditional UPDATE) as-is:
if `expected_hash` differs from the current DB value (reuse of an already-
rotated old token, or guessing a wrong token), that function revokes the
session and raises `RefreshReuseDetected`, so we simply propagate it.

task-9420 F4 — the `get_active()` read above and the rotation UPDATE below are
separate statements, so a logout completed on another connection in between
must not let rotation still succeed (the WHERE clause in `rotate_refresh()`
now re-checks active/unexpired). We translate that repository-level race
signal back into the same `RefreshSessionNotFoundError`/`RefreshTokenExpiredError`
this use case already raises for the non-racy case, keeping it distinct from
`RefreshReuseDetected` (reuse implies possible token compromise; this does not).
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
    """`session_id` does not exist or has already been revoked. Because the
    rotation target itself is absent we do not call
    `session_repository.rotate_refresh()`, so we distinguish this as a
    separate exception from `RefreshReuseDetected` (both map to 401
    `AUTH_SESSION_REVOKED`, but the root causes differ)."""


class RefreshTokenExpiredError(Exception):
    """Session is active but has passed the 14-day absolute expiry
    (`expires_at`) — distinguish from reuse detection (compromise suspicion)
    and map to 401 `AUTH_TOKEN_EXPIRED` (prompt re-login, not a compromise
    alert target)."""


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
            raise RefreshSessionNotFoundError(f"session_id={session_id}: no active session")

        now = datetime.now(timezone.utc)
        if session.expires_at <= now:
            await session_repository.revoke(conn, session_id, reason="expired")
            raise RefreshTokenExpiredError(f"session_id={session_id}: refresh expired")

        new_plaintext, new_hash = TokenIssuer.issue_refresh()
        try:
            rotated = await session_repository.rotate_refresh(
                conn,
                session_id,
                expected_hash=hash_refresh_token(refresh_token),
                new_hash=new_hash,
            )
        except session_repository.RefreshSessionRevokedError as exc:
            raise RefreshSessionNotFoundError(
                f"session_id={session_id}: 회전 시도 중 세션이 이미 revoke됨"
            ) from exc
        except session_repository.RefreshSessionExpiredMidRotationError as exc:
            raise RefreshTokenExpiredError(
                f"session_id={session_id}: 회전 시도 중 refresh 만료"
            ) from exc

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
