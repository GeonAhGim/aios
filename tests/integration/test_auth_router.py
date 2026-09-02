"""16번대 통합테스트 — 실제 FastAPI 앱 + 실제 dev DB, HTTP 계층까지 왕복.

httpx ASGITransport로 실제 uvicorn 없이 앱을 직접 구동한다 —
app.router.lifespan_context로 main.py의 lifespan(asyncpg pool 생성)을
그대로 태운다.
"""
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.integration.mfa_clock import mfa_clock_shifted, totp_at

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False — 도메인 예외(AuthError 등)는 전역
        # Exception 핸들러(src/api/contracts/handlers.py)가 처리하는데,
        # FastAPI가 그 핸들러를 ServerErrorMiddleware로 승격시켜 정상
        # 응답을 보낸 뒤에도 예외를 다시 던진다(Starlette 설계) — httpx
        # ASGITransport 기본값(True)은 그걸 테스트 프로세스로 재전파해
        # 정상적으로 처리된 4xx 응답까지 테스트 실패로 만든다
        # (tests/unit/api/contracts/test_handlers.py에 동일 근거 기록됨).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def test_register_returns_access_token(client):
    response = await client.post(
        "/auth/register", json={"email": _unique_email(), "password": STRONG_PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert "trace_id" in body["meta"]
    data = body["data"]
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_register_rejects_weak_password(client):
    response = await client.post(
        "/auth/register", json={"email": _unique_email(), "password": "short"}
    )

    assert response.status_code in (400, 422)


async def test_login_after_register_succeeds(client):
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )

    assert response.status_code == 200
    assert "access_token" in response.json()["data"]


async def test_suspended_account_loses_access_mid_session_not_just_at_next_login(client, pool):
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


async def test_login_with_wrong_password_rejected(client):
    email = _unique_email()
    await client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    response = await client.post(
        "/auth/login", json={"email": email, "password": "WrongPassword1!"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "AUTH_INVALID_CREDENTIALS"
    assert "trace_id" in body


async def test_get_me_requires_authentication(client):
    response = await client.get("/users/me")

    assert response.status_code == 401


async def test_get_me_returns_current_user_with_valid_token(client):
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


async def test_get_me_rejects_invalid_token(client):
    response = await client.get(
        "/users/me", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401


async def test_mfa_setup_and_verify_round_trip(client):
    import pyotp

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    assert setup_response.status_code == 200
    secret = setup_response.json()["data"]["secret"]

    code = pyotp.totp.TOTP(secret).now()
    verify_response = await client.post(
        "/auth/mfa/verify", json={"totp_code": code}, headers=headers
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["data"]["mfa_enabled"] is True

    login_without_code = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert login_without_code.status_code == 401

    # docs/RED_TEAM_FINDINGS.md #13 반영 — 같은 30초 구간의 코드는 재사용
    # 거부 대상이라, 로그인용 코드는 다음 구간에서 새로 받아야 한다. 실시간
    # 31초 대기 대신 MfaService 시계를 31초 앞당긴다(전수감사 §9).
    with mfa_clock_shifted(app, 31) as shifted_now:
        login_code = totp_at(secret, shifted_now())
        login_with_code = await client.post(
            "/auth/login",
            json={"email": email, "password": STRONG_PASSWORD, "totp_code": login_code},
        )
    assert login_with_code.status_code == 200


async def test_mfa_resetup_without_password_rejected_when_already_enabled(client):
    """레드팀 감사 #11 후속 — 이미 켜진 MFA를 탈취한 Bearer 토큰만으로
    (비밀번호 없이) 재설정해 secret을 갈아치울 수 있으면 안 된다."""
    import pyotp

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["data"]["secret"]
    code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    resetup_response = await client.post("/auth/mfa/setup", headers=headers)
    assert resetup_response.status_code == 403


async def test_mfa_resetup_with_correct_password_succeeds(client):
    import pyotp

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    old_secret = setup_response.json()["data"]["secret"]
    code = pyotp.totp.TOTP(old_secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    # docs/RED_TEAM_FINDINGS.md #13 반영 — 위 verify()가 이미 이 구간의
    # 코드를 소비했으므로 재인증용 코드는 다음 구간에서 새로 받아야 한다.
    with mfa_clock_shifted(app, 31) as shifted_now:
        reauth_code = totp_at(old_secret, shifted_now())
        resetup_response = await client.post(
            "/auth/mfa/setup",
            json={"password": STRONG_PASSWORD, "totp_code": reauth_code},
            headers=headers,
        )
    assert resetup_response.status_code == 200
    new_secret = resetup_response.json()["data"]["secret"]
    assert new_secret != old_secret


async def test_logout_requires_authentication(client):
    response = await client.post("/auth/logout")

    assert response.status_code == 401
