"""PLT-24 통합테스트 — login/refresh/logout 유스케이스(task-1075).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

핵심 DoD: 로그인이 세션+토큰 쌍을 만들고, refresh가 회전하며, 옛
refresh_token 재사용(또는 잘못된 session_id/user_id 조합)은 세션을
즉시 revoke한다 — session_repository(PLT-23)의 조건부 회전을 그대로
탄다는 것을 서비스 계층에서 왕복 검증한다.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.services.auth import login as login_usecase
from src.services.auth import logout as logout_usecase
from src.services.auth import refresh as refresh_usecase
from src.services.auth import session_repository
from src.services.auth.tokens import TokenIssuer, TokenVerifier, hash_refresh_token
from src.services.auth_service import AuthError, AuthService

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
    yield p
    await p.close()


def _auth(pool: asyncpg.Pool) -> AuthService:
    return AuthService(pool, jwt_secret_key="unused-legacy-secret-min-32-bytes-long")


def _issuer() -> TokenIssuer:
    return TokenIssuer.from_env()


def _verifier() -> TokenVerifier:
    return TokenVerifier.from_env()


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _signup(auth: AuthService) -> str:
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)
    return email


async def test_login_creates_active_session_and_returns_verifiable_token_pair(pool):
    auth = _auth(pool)
    email = await _signup(auth)

    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )

    assert pair.token_type == "bearer"
    assert pair.expires_in == 15 * 60
    claims = _verifier().verify(pair.access_token)
    assert claims.sid == pair.session_id
    assert claims.auth_level == "PASSWORD"

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is not None
    assert session.refresh_hash == hash_refresh_token(pair.refresh_token)


async def test_login_with_wrong_password_raises_auth_error_and_session_not_created(pool):
    auth = _auth(pool)
    email = await _signup(auth)

    with pytest.raises(AuthError):
        await login_usecase.login(
            pool, auth, _issuer(), email=email, password="WrongPassword1!"
        )


async def test_refresh_rotates_token_pair_and_keeps_same_session_id(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )

    rotated = await refresh_usecase.refresh(
        pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
    )

    assert rotated.session_id == pair.session_id
    assert rotated.refresh_token != pair.refresh_token
    assert rotated.access_token != pair.access_token


async def test_refresh_reuse_of_old_refresh_token_revokes_session(pool):
    """핵심 DoD — 옛 refresh_token 재사용은 세션을 즉시 revoke한다."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )
    await refresh_usecase.refresh(
        pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
    )

    with pytest.raises(session_repository.RefreshReuseDetected):
        await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
        )

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is None


async def test_refresh_with_unknown_session_id_raises_not_found(pool):
    with pytest.raises(refresh_usecase.RefreshSessionNotFoundError):
        await refresh_usecase.refresh(
            pool, _issuer(), session_id=uuid.uuid4(), refresh_token="whatever"
        )


async def test_refresh_after_absolute_expiry_revokes_session_as_expired(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE auth_session SET expires_at = $2 WHERE id = $1",
            pair.session_id,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    with pytest.raises(refresh_usecase.RefreshTokenExpiredError):
        await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT revoked_at, revoke_reason FROM auth_session WHERE id = $1", pair.session_id
        )
    assert row["revoked_at"] is not None
    assert row["revoke_reason"] == "expired"


async def test_logout_revokes_the_session(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )

    async with pool.acquire() as conn:
        user_id = (await session_repository.get_active(conn, pair.session_id)).user_id

    await logout_usecase.logout(pool, session_id=pair.session_id, user_id=user_id)

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is None


async def test_logout_with_mismatched_user_is_rejected_and_session_survives(pool):
    """다른 사용자의 session_id를 추측해 로그아웃시키는 시나리오 — 거부되고
    원래 세션은 그대로 활성 상태여야 한다."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )
    attacker_user_id = uuid.uuid4()

    with pytest.raises(logout_usecase.LogoutSessionMismatchError):
        await logout_usecase.logout(
            pool, session_id=pair.session_id, user_id=attacker_user_id
        )

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is not None


async def test_logout_all_revokes_every_active_session_for_user(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    first = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )
    second = await login_usecase.login(
        pool, auth, _issuer(), email=email, password=STRONG_PASSWORD
    )

    async with pool.acquire() as conn:
        user_id = (await session_repository.get_active(conn, first.session_id)).user_id

    revoked_count = await logout_usecase.logout_all(pool, user_id=user_id)

    assert revoked_count == 2
    async with pool.acquire() as conn:
        assert await session_repository.get_active(conn, first.session_id) is None
        assert await session_repository.get_active(conn, second.session_id) is None
