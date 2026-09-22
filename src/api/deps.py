"""Station 16 — FastAPI shared dependencies.

get_pool() returns app.state.pool as-is — reuses the pool initialized
during app assembly (main.py lifespan) and passes it as asyncpg.Pool
to service constructors (not a SQLAlchemy session — see deviation note
at the top of main.py).

get_current_user() validates the Authorization: Bearer <JWT> header
via PLT-23 `TokenVerifier` (per-kid key, fixed alg HS256) and also
checks that the `auth_session` referenced by `sid` is still active
(`revoked_at IS NULL`) (§3.4 "Check revoked_at on every request").
Unlike the legacy JWT verifier that only checked signature and claims,
a revoked session (logout / reuse detection) is rejected immediately
even if the access token has not expired. get_current_verifier/
get_current_admin enforce RBAC flags from §15.6 of the auth doc
(is_verifier / is_platform_admin).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from src.api.contracts.exception_mapping import SessionRevokedError
from src.core.event_bus.bus import EventBus
from src.services.auth import session_repository
from src.services.auth.tokens import AuthLevel, TokenIssuer, TokenVerifier
from src.services.auth_service import AuthError, AuthService, User, get_user_by_id
from src.services.mfa_service import MfaService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class AuthenticatedUser(User):
    """PLT-24 — The actual return type of `get_current_user()`. Subclass of
    `User` so existing routers (15+) declared with `user: User = Depends(get_current_user)` continue to work (extra fields are simply ignored) — only new routers that need `session_id`/`auth_level` (e.g., `/auth/logout`) should declare this type."""

    session_id: UUID
    auth_level: AuthLevel


async def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


def get_token_issuer() -> TokenIssuer:
    return TokenIssuer.from_env()


def get_token_verifier() -> TokenVerifier:
    return TokenVerifier.from_env()


async def get_event_bus(request: Request) -> EventBus:
    """Returns the InProcessEventBus initialized during app assembly
    (main.py lifespan). Tests override this dependency via dependency_overrides
    to avoid CRITICAL retry delays (up to 31s, §5.5) caused by missing
    dispatchers (SMTP/FCM)."""
    event_bus: EventBus = request.app.state.event_bus
    return event_bus


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    pool: asyncpg.Pool = Depends(get_pool),
    verifier: TokenVerifier = Depends(get_token_verifier),
) -> AuthenticatedUser:
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    # TokenInvalidError/TokenExpiredError(tokens.py) propagate as-is —
    # exception_mapping.py maps them to 401 AUTH_TOKEN_INVALID/AUTH_TOKEN_EXPIRED,
    # so there is no need to wrap them in a new raw HTTPException here.
    claims = verifier.verify(token)

    user = await get_user_by_id(pool, claims.sub)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "존재하지 않는 사용자입니다.")
    if user.status in ("SUSPENDED", "DELETED"):
        # AuthService.authenticate() rejects SUSPENDED/DELETED at login,
        # but the issued JWT remains valid until expiry — without this
        # per-request check, suspended accounts could continue using the
        # API with their existing token (discovered during router wiring;
        # login-time check alone proved insufficient).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "정지되었거나 삭제된 계정입니다.")

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, claims.sid)
    if session is None:
        raise SessionRevokedError(f"session_id={claims.sid}: 세션이 폐기되었습니다")

    return AuthenticatedUser(
        **user.model_dump(), session_id=claims.sid, auth_level=claims.auth_level
    )


async def reauthenticate(
    auth: AuthService, user: User, password: str, totp_code: str | None = None
) -> None:
    """Re-verifies password (TOTP if MFA is active) — prevents Bearer token
    theft from succeeding on sensitive actions like fund transfers or security
    changes, even for already-logged-in sessions. Reuses AuthService.authenticate()
    directly (login-capable = re-auth success, no new verification logic).
    Extracts the same principle shared by whitelist/deletion routers in users.py
    into a shared entry point."""
    try:
        await auth.authenticate(user.email, password, totp_code=totp_code)
    except AuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "재인증에 실패했습니다.") from exc


async def get_current_verifier(user: User = Depends(get_current_user)) -> User:
    if not user.is_verifier:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "검증담당자 권한이 필요합니다.")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "운영자 권한이 필요합니다.")
    return user


def get_mfa_service(
    request: Request, pool: asyncpg.Pool = Depends(get_pool)
) -> MfaService:
    secrets = request.app.state.secrets
    return MfaService(pool, encryption_key=secrets.credential_encryption_key.get_secret_value())


def get_auth_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    mfa: MfaService = Depends(get_mfa_service),
) -> AuthService:
    secrets = request.app.state.secrets
    return AuthService(
        pool,
        jwt_secret_key=secrets.jwt_secret_key.get_secret_value(),
        jwt_algorithm=secrets.jwt_algorithm,
        jwt_expire_minutes=secrets.jwt_expire_minutes,
        verify_totp=mfa.verify_totp_for_login,
    )
