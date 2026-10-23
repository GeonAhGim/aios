"""Security / permission / account-lifecycle tests for auth router.

계정 정지, 토큰 검증, 관리자 권한, 중복 가입 등 보안 관련 테스트는
별도 파일로 분리하여 test_auth_router.py의 LOC를 500줄 경고선 아래로
유지한다.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


# ── suspended account tests ────────────────────────────────────────────────


async def test_suspended_account_loses_access_mid_session_not_just_at_next_login(
    client: AsyncClient, pool: asyncpg.Pool
) -> None:
    """73번 §6 규칙1 "A command requires an ACTIVE membership" — P0 단일-owner
    스콥에서는 "ACTIVE membership"이 곧 "이 계정 자체가 ACTIVE"다(TenantContext.role
    주석 참조, household/organization membership은 아직 없음). deps.py의
    get_current_user()가 이미 매 요청마다 SUSPENDED/DELETED를 막는다는 주석이
    있었지만(로그인 시점 검사만으로는 불충분하다는 실제 발견 기록) 이를
    고정하는 회귀테스트가 없었다 — 발급된 토큰이 만료 전까지 계속 유효한
    시나리오를 실제로 재현한다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {register_response.json()['data']['access_token']}"}

    before_suspend = await client.get("/v1/foundation/trust/status", headers=headers)
    assert before_suspend.status_code == 200

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET status = 'SUSPENDED' WHERE email = $1", email)

    after_suspend = await client.get("/v1/foundation/trust/status", headers=headers)
    assert after_suspend.status_code == 401


async def test_suspended_account_check_with_invalid_token(client: AsyncClient) -> None:
    """음성: 유효하지 않은 토큰으로 trust/status 에 접근하면 401을 반환한다."""
    response = await client.get(
        "/v1/foundation/trust/status", headers={"Authorization": "Bearer invalid-token"}
    )
    assert response.status_code == 401


# ── login / password validation ────────────────────────────────────────────


async def test_login_after_register_succeeds(client: AsyncClient) -> None:
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    response = await client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})

    assert response.status_code == 200
    assert "access_token" in response.json()["data"]


async def test_login_with_wrong_password_rejected(client: AsyncClient) -> None:
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    response = await client.post(
        "/auth/login", json={"email": email, "password": "WrongPassword1!"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "AUTH_INVALID_CREDENTIALS"
    assert "trace_id" in body


# ── /users/me ──────────────────────────────────────────────────────────────


async def test_get_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/users/me")

    assert response.status_code == 401


async def test_get_me_returns_current_user_with_valid_token(client: AsyncClient) -> None:
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["email"] == email
    assert body["mfa_enabled"] is False


async def test_get_me_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get("/users/me", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401


# ── logout ─────────────────────────────────────────────────────────────────


async def test_logout_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/auth/logout")

    assert response.status_code == 401


async def test_logout_endpoint_revokes_session(client: AsyncClient) -> None:
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    pair = register_response.json()["data"]
    headers = {"Authorization": f"Bearer {pair['access_token']}"}

    response = await client.post("/auth/logout", headers=headers)
    assert response.status_code == 200

    refresh_after_logout = await client.post(
        "/auth/refresh",
        json={"session_id": pair["session_id"], "refresh_token": pair["refresh_token"]},
    )
    assert refresh_after_logout.status_code == 401


async def test_logout_all_endpoint_revokes_every_session(client: AsyncClient) -> None:
    email = _unique_email()
    first = (
        await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    ).json()["data"]
    second = (
        await client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    ).json()["data"]

    response = await client.post(
        "/auth/logout-all", headers={"Authorization": f"Bearer {first['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["revoked_count"] == 2
    refresh_first = await client.post(
        "/auth/refresh",
        json={"session_id": first["session_id"], "refresh_token": first["refresh_token"]},
    )
    refresh_second = await client.post(
        "/auth/refresh",
        json={"session_id": second["session_id"], "refresh_token": second["refresh_token"]},
    )
    assert refresh_first.status_code == 401
    assert refresh_second.status_code == 401


# ── refresh ────────────────────────────────────────────────────────────────


async def test_refresh_endpoint_rotates_token_pair(client: AsyncClient) -> None:
    """PLT-24 — spec §9 DoD "기존 test_auth_router.py는 토큰 쌍 응답으로
    갱신"의 /auth/refresh 왕복 부분. 서비스 계층은
    tests/integration/services/auth/test_login_refresh_logout.py가 이미
    검증하지만, 라우터 배선(RefreshRequest 바인딩·ok() 봉투)은 여기서만
    실제 HTTP로 확인된다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    pair = register_response.json()["data"]

    response = await client.post(
        "/auth/refresh",
        json={"session_id": pair["session_id"], "refresh_token": pair["refresh_token"]},
    )

    assert response.status_code == 200
    rotated = response.json()["data"]
    assert rotated["session_id"] == pair["session_id"]
    assert rotated["refresh_token"] != pair["refresh_token"]
    assert rotated["access_token"] != pair["access_token"]


async def test_refresh_endpoint_rejects_reused_refresh_token(client: AsyncClient) -> None:
    """핵심 DoD — 옛 refresh_token 재사용은 라우터 계층에서도 401
    AUTH_SESSION_REVOKED로 거부된다(RefreshReuseDetected 전파 확인)."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    pair = register_response.json()["data"]
    refresh_body = {"session_id": pair["session_id"], "refresh_token": pair["refresh_token"]}
    await client.post("/auth/refresh", json=refresh_body)

    response = await client.post("/auth/refresh", json=refresh_body)

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_SESSION_REVOKED"


# ── negative / invariant tests ─────────────────────────────────────────────


async def test_register_rejects_weak_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register", json={"email": _unique_email(), "password": "short"}
    )

    assert response.status_code in (400, 422)


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    """불변식 위반 — 이미 가입된 이메일로 재가입하면 401로 거부해야 한다.

    AuthService.signup() 가 AuthError("이미 등록된 이메일입니다.")를
    던지고, exception_mapping이 AuthError → 401 AUTH_INVALID_CREDENTIALS로
    매핑한다(계정 존재 여부가 유출되지 않도록).
    """
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "AUTH_INVALID_CREDENTIALS"


async def test_admin_endpoint_rejects_non_admin(client: AsyncClient) -> None:
    """불변식 위반 — admin-only 엔드포인트에 일반 사용자가 접근하면 403/401로 거부해야 한다.

    admin.py 라우터는 get_current_admin 의존성으로 admin 체크를 한다.
    일반 계정이 /admin/users 엔드포인트에 접근하면 거부된다.
    """
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 일반 사용자가 admin 엔드포인트 접근 시도
    response = await client.get("/admin/users", headers=headers)
    assert response.status_code in (401, 403)
