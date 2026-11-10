"""PLT-24 DEEPEN(task-9377) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_login_refresh_logout.py`/`test_lockout_atomic.py`가 이미 login/refresh/
logout/lockout의 주된 negative/실패주입/동시성/성능 증거를 갖고 있으므로,
여기서는 그 파일들이 다루지 않은 세 공백만 메운다:

1. `refresh()`가 *한 번도 회전된 적 없는* 세션에서 틀린 refresh_token으로
   최초 시도되는 경우 -- 기존 `test_refresh_reuse_of_old_refresh_token_
   revokes_session`은 "정상 회전 후 옛 토큰 재사용"만 다룬다. 최초 추측
   시도도 `rotate_refresh()`의 105 조건부 UPDATE에서 동일하게 걸려
   `RefreshReuseDetected`가 나고 세션이 즉시 revoke돼야 한다(토큰 탈취
   추측 공격 방어).
2. `logout()`이 이미 revoke된(또는 존재하지 않는) session_id에 대해
   호출됐을 때 `LogoutSessionMismatchError`를 던지지 않고 조용히 no-op으로
   끝나는지(멱등) -- `get_active`가 None을 반환하는 분기는 기존 테스트가
   실측하지 않는다. `logout_all()`이 활성 세션이 0개인 사용자에게 정확히
   0을 반환하는지도 함께 확인한다.
3. `issue_token_pair`(login/register 공유 경로)가 세션 생성 단계
   (`session_repository.insert_session`)에서 예상치 못한 의존성 예외를
   삼키지 않고 그대로 전파하는지(fail-closed) -- 삼키면 세션 없이 토큰만
   발급되는 정합성 붕괴가 조용히 성공으로 위장된다.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

from src.services.auth import login as login_usecase
from src.services.auth import logout as logout_usecase
from src.services.auth import session_repository
from src.services.auth.tokens import TokenIssuer, hash_refresh_token
from src.services.auth_service import AuthService

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


def _unique_email() -> str:
    return f"test-init-deepen-{uuid.uuid4().hex}@example.com"


async def _signup(auth: AuthService) -> str:
    email = _unique_email()
    await auth.signup(email, STRONG_PASSWORD)
    return email


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_refresh_with_wrong_token_on_never_rotated_session_revokes_it(pool):
    """negative: 한 번도 회전된 적 없는 세션에서 틀린 refresh_token을
    최초로 시도해도 -- `test_refresh_reuse_of_old_refresh_token_revokes_session`
    이 다루는 "정상 회전 후 재사용"과 달리, 이 세션은 아직 한 번도 회전된
    적이 없다 -- 105 조건부 UPDATE가 `expected_hash` 불일치로 걸려
    `RefreshReuseDetected`가 나고 세션이 즉시 revoke돼야 한다."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

    async with pool.acquire() as conn:
        with pytest.raises(session_repository.RefreshReuseDetected):
            await session_repository.rotate_refresh(
                conn,
                pair.session_id,
                expected_hash=hash_refresh_token("guessed-wrong-refresh-token"),
                new_hash=hash_refresh_token("irrelevant-new-token"),
            )

    async with pool.acquire() as conn:
        session = await session_repository.get_active(conn, pair.session_id)
    assert session is None, "틀린 refresh_token 최초 추측 시도도 세션을 즉시 revoke해야 한다"


async def test_logout_on_already_revoked_session_is_silent_noop(pool):
    """negative: 이미 revoke된 session_id로 logout()을 다시 호출해도
    `LogoutSessionMismatchError`를 던지지 않는다 -- `get_active`가 None을
    반환하는 분기에서는 소유권 비교 자체를 건너뛰고, `revoke()`의 자체
    멱등성(`revoked_at IS NULL` 조건)에 맡긴다."""
    auth = _auth(pool)
    email = await _signup(auth)
    pair = await login_usecase.login(pool, auth, _issuer(), email=email, password=STRONG_PASSWORD)

    async with pool.acquire() as conn:
        user_id = (await session_repository.get_active(conn, pair.session_id)).user_id

    await logout_usecase.logout(pool, session_id=pair.session_id, user_id=user_id)

    # 세션은 이미 revoke됐다 -- 아무 user_id(심지어 무관한 값)로 다시 호출해도
    # 예외 없이 조용히 끝나야 한다.
    await logout_usecase.logout(pool, session_id=pair.session_id, user_id=uuid.uuid4())


async def test_logout_all_for_user_with_no_active_sessions_returns_zero(pool):
    """negative: 활성 세션이 하나도 없는 사용자에게 logout_all()을 호출하면
    예외 없이 정확히 0을 반환해야 한다(음수/None으로 위장하지 않음)."""
    auth = _auth(pool)
    await _signup(auth)  # 세션은 만들지 않는다.

    revoked_count = await logout_usecase.logout_all(pool, user_id=uuid.uuid4())

    assert revoked_count == 0


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_issue_token_pair_propagates_session_insert_failure(
    pool, monkeypatch: pytest.MonkeyPatch
):
    """실패 주입: `issue_token_pair`가 위임하는 `session_repository.
    insert_session`이 의존성 계층 예외(드라이버/네트워크)로 실패하면, 이를
    삼켜 세션 없이 토큰만 발급되는 조용한 성공으로 위장하지 않고 원 예외를
    그대로 전파해야 한다(fail-closed)."""
    auth = _auth(pool)
    email = await _signup(auth)
    user = await auth.authenticate(email, STRONG_PASSWORD)

    async def _boom(conn, **kwargs):
        raise RuntimeError("simulated session insert failure")

    monkeypatch.setattr(session_repository, "insert_session", _boom)

    with pytest.raises(RuntimeError, match="simulated session insert failure"):
        await login_usecase.issue_token_pair(pool, _issuer(), user)
