"""PLT-24 통합테스트 — login/refresh/logout 유스케이스(task-1075).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.4, §9 PLT-24.

핵심 DoD: 로그인이 세션+토큰 쌍을 만들고, refresh가 회전하며, 옛
refresh_token 재사용(또는 잘못된 session_id/user_id 조합)은 세션을
즉시 revoke한다 — session_repository(PLT-23)의 조건부 회전을 그대로
탄다는 것을 서비스 계층에서 왕복 검증한다.
"""

from __future__ import annotations

import asyncio
import os
import time
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

# ADR-2026-09-09-C §행 "축별 성능 예산" — auth refresh 전용 항목은 없다.
# refresh()는 Argon2 비밀번호 검증을 타지 않는 순수 단일 DB round-trip
# 트랜잭션(105 조건부 UPDATE)이므로, 가장 근접한 비교축인
# "주문 제출→ACK p95 50ms(paper)"(단일 DB 트랜잭션 왕복)을 차용한다.
REFRESH_P95_BUDGET_MS = 50.0


async def _rotate_refresh_without_conditional_check(
    conn: asyncpg.Connection, session_id: uuid.UUID, new_hash: str
) -> None:
    """게이트 적색 재현 전용 — `session_repository.rotate_refresh()`가 쓰는
    105 조건부 UPDATE(`expected_hash` 일치 검사)를 의도적으로 생략한
    버전이다. 프로덕션 코드(session_repository.py)는 건드리지 않고, "만약
    조건부 UPDATE가 없었다면 동시 재사용 공격이 감지되지 않았을 것"을 이
    헬퍼로 재현한다."""
    await conn.execute(
        "UPDATE auth_session SET refresh_hash = $2, rotated_at = now() WHERE id = $1",
        session_id,
        new_hash,
    )


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

    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

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
        await login_usecase.login(pool, auth, _issuer(), email=email, password="WrongPassword1!")


async def test_refresh_rotates_token_pair_and_keeps_same_session_id(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

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
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)
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
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

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
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

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
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)
    attacker_user_id = uuid.uuid4()

    with pytest.raises(logout_usecase.LogoutSessionMismatchError):
        await logout_usecase.logout(pool, session_id=pair.session_id, user_id=attacker_user_id)

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is not None


async def test_logout_all_revokes_every_active_session_for_user(pool):
    auth = _auth(pool)
    email = await _signup(auth)
    first = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)
    second = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

    async with pool.acquire() as conn:
        user_id = (await session_repository.get_active(conn, first.session_id)).user_id

    revoked_count = await logout_usecase.logout_all(pool, user_id=user_id)

    assert revoked_count == 2
    async with pool.acquire() as conn:
        assert await session_repository.get_active(conn, first.session_id) is None
        assert await session_repository.get_active(conn, second.session_id) is None


# ----------------------------------------------------------------------
# 실패 주입 — 동시 refresh 경합(탈취된 토큰이 원 소유자와 동시에 재생됨)
# ----------------------------------------------------------------------


async def test_concurrent_refresh_with_same_token_revokes_session_fail_closed(pool):
    """실패 주입 — `test_refresh_reuse_of_old_refresh_token_revokes_session`은
    순차 재사용만 검증한다. 이 테스트는 같은 refresh_token으로 두 요청이
    *동시에* 경합하는(탈취된 토큰이 원 소유자와 동시에 재생되는, 또는
    이중 클릭/재시도로 인한 레이스) 상황을 asyncio.gather로 재현한다.
    `rotate_refresh()`의 105 조건부 UPDATE 덕분에 정확히 한쪽만 새 토큰을
    받지만, 진 쪽이 타는 `ConcurrencyConflictError` 처리 경로(`revoke(...,
    reason="refresh_reuse")`)는 "누가 이겼는지" 구분하지 않고 세션 자체를
    무조건 revoke한다 — 그 결과 방금 이긴 쪽이 받은 새 토큰도 즉시
    무효화된다. CLAUDE.md 기본 정책("fail-closed")대로 레이스가 감지되면
    양쪽 다 재로그인을 요구하는 것이 의도된 동작임을 증명한다(승자가
    안전하게 살아남는다는 낙관적 가정은 틀렸다)."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

    async def attempt():
        return await refresh_usecase.refresh(
            pool, _issuer(), session_id=pair.session_id, refresh_token=pair.refresh_token
        )

    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"승자가 정확히 1명이어야 한다: {results}"
    assert len(failures) == 1
    assert isinstance(failures[0], session_repository.RefreshReuseDetected)

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is None, (
        "fail-closed: 레이스가 감지되면 방금 이긴 쪽의 새 토큰도 세션과 함께 revoke돼야 한다"
    )


# ----------------------------------------------------------------------
# 수치 성능 단언 — refresh() p95 지연시간 (ADR-2026-09-09-C 축별 예산 차용)
# ----------------------------------------------------------------------


@pytest.mark.perf
async def test_refresh_latency_p95_within_borrowed_order_ack_budget(pool):
    """수치 성능 단언 — refresh() 1회 호출(조건부 UPDATE 회전 + JWT 발급)의
    p95 지연시간이 차용 예산(50ms) 이내여야 한다. Argon2 해시 비용이 섞이지
    않도록 세션은 로그인 유스케이스가 아니라 `session_repository.insert_session`
    으로 직접 만들어, refresh() 자체의 고정비만 측정한다."""
    auth = _auth(pool)
    issuer = _issuer()
    email = await _signup(auth)
    seed = await login_usecase.login(pool, auth, issuer, email=email, password=STRONG_PASSWORD)
    async with pool.acquire() as conn:
        user_id = (await session_repository.get_active(conn, seed.session_id)).user_id

    n = 30
    latencies: list[float] = []
    for _ in range(n):
        plain, refresh_hash = TokenIssuer.issue_refresh()
        async with pool.acquire() as conn:
            session = await session_repository.insert_session(
                conn,
                user_id=user_id,
                tenant_id=user_id,
                refresh_hash=refresh_hash,
                ip_hash=None,
                ua_hash=None,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                auth_level="PASSWORD",
            )
        start = time.perf_counter()
        await refresh_usecase.refresh(pool, issuer, session_id=session.id, refresh_token=plain)
        latencies.append(time.perf_counter() - start)

    latencies.sort()
    p95_ms = latencies[int(n * 0.95)] * 1000
    assert p95_ms < REFRESH_P95_BUDGET_MS, (
        f"refresh() p95 지연시간 {p95_ms:.4f}ms가 예산 {REFRESH_P95_BUDGET_MS}ms 초과"
    )


# ----------------------------------------------------------------------
# 게이트 적색 재현 — 105 조건부 UPDATE가 없으면 동시 재사용이 감지되지 않는다
# ----------------------------------------------------------------------


async def test_gate_red_reproduction_without_conditional_update_concurrent_reuse_undetected(
    pool,
):
    """게이트 적색 재현 — 위 `test_concurrent_refresh_with_same_token_revokes_session_fail_closed`
    (실패 주입)가 실제로 무엇을 막고 있는지 증명한다. `rotate_refresh()`의
    105 조건부 UPDATE(`expected_hash` 일치 검사)를 생략한
    `_rotate_refresh_without_conditional_check`로 똑같은 동시 경합을
    재현하면, 예외 없이 둘 다 성공한다 — 조건부 UPDATE가 없었다면 위
    실패 주입 테스트가 검증하는 "승자는 정확히 1명" 단언이 실제로
    깨졌을 것이라는 증거다."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

    async def broken_attempt():
        async with pool.acquire() as conn:
            await _rotate_refresh_without_conditional_check(
                conn, pair.session_id, hash_refresh_token(uuid.uuid4().hex)
            )

    results = await asyncio.gather(broken_attempt(), broken_attempt(), return_exceptions=True)

    assert results == [None, None], (
        f"조건부 UPDATE 없이는 동시 재사용이 예외 없이 둘 다 성공한다 — 결과: {results}"
    )
