"""16번대 통합테스트 — MFA(TOTP) 관련 라우터 왕복.

test_auth_router.py(ADR-2026-09-10-C §7, 500라인 관측선)에서 분리된 MFA
전용 회귀 모음 — 기본 회원가입/로그인/세션 테스트는 test_auth_router.py에
남는다.

httpx ASGITransport로 실제 uvicorn 없이 앱을 직접 구동한다 —
app.router.lifespan_context로 main.py의 lifespan(asyncpg pool 생성)을
그대로 태운다.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.integration.mfa_clock import mfa_clock_frozen, totp_at

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
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


async def test_mfa_setup_and_verify_round_trip(client: AsyncClient) -> None:
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    assert setup_response.status_code == 200
    secret = setup_response.json()["data"]["secret"]

    # esc-ci-cbb8b9c62497 — 실시간 코드는 서버 검증과의 30초 구간 경계 레이스가
    # 있어(valid_window=0) 시계를 고정한다.
    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        verify_response = await client.post(
            "/auth/mfa/verify", json={"totp_code": totp_at(secret, frozen_now)}, headers=headers
        )
    assert verify_response.status_code == 200, verify_response.text
    assert verify_response.json()["data"]["mfa_enabled"] is True

    login_without_code = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert login_without_code.status_code == 401

    # docs/RED_TEAM_FINDINGS.md #13 반영 — 같은 30초 구간의 코드는 재사용
    # 거부 대상이라, 로그인용 코드는 다음 구간에서 새로 받아야 한다. 실시간
    # 31초 대기 대신 MfaService 시계를 다음 구간의 한 시각으로 고정한다
    # (shifted는 실시간을 다시 읽어 경계 레이스가 남는다).
    login_at = frozen_now + timedelta(seconds=31)
    with mfa_clock_frozen(app, login_at):
        login_with_code = await client.post(
            "/auth/login",
            json={
                "email": email,
                "password": STRONG_PASSWORD,
                "totp_code": totp_at(secret, login_at),
            },
        )
    assert login_with_code.status_code == 200, login_with_code.text


async def test_mfa_resetup_without_password_rejected_when_already_enabled(
    client: AsyncClient,
) -> None:
    """레드팀 감사 #11 후속 — 이미 켜진 MFA를 탈취한 Bearer 토큰만으로
    (비밀번호 없이) 재설정해 secret을 갈아치울 수 있으면 안 된다.

    실시간 TOTP(`pyotp...now()`)로 코드를 만들면, 코드 생성(클라이언트)과
    `/auth/mfa/verify` 처리(서버) 사이에 우연히 30초 구간 경계를 넘어 같은
    코드가 무효 처리되는 드문 레이스가 있었다(esc-ci-cbb8b9c62497 — 전체
    스위트 실행 중 드물게만 재현). `mfa_clock_frozen`으로 시계를 고정해
    실서비스 경로(라우터→MfaService.setup/verify)는 그대로 태우되 "몇
    시인지"만 결정론적으로 만든다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        secret = setup_response.json()["data"]["secret"]
        code = totp_at(secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

        resetup_response = await client.post("/auth/mfa/setup", headers=headers)

    assert resetup_response.status_code == 403


async def test_mfa_resetup_with_correct_password_succeeds(client: AsyncClient) -> None:
    """esc-ci-67bd83edb539 — 옛 코드는 초기 setup/verify 코드를
    `pyotp...now()`(실시간)로 만들어 서버 실시간과 비교했다.
    test_mfa_resetup_without_password_rejected_when_already_enabled에서
    이미 잡은 것과 같은 30초 구간 경계 레이스(esc-ci-cbb8b9c62497)가 이
    테스트에는 반영되지 않아 드물게 재현됐다 — mfa_clock_frozen으로 두
    구간 모두 결정론화한다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    frozen_now = datetime.now(timezone.utc)
    with mfa_clock_frozen(app, frozen_now):
        setup_response = await client.post("/auth/mfa/setup", headers=headers)
        old_secret = setup_response.json()["data"]["secret"]
        code = totp_at(old_secret, frozen_now)
        await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    # docs/RED_TEAM_FINDINGS.md #13 반영 — 위 verify()가 이미 이 구간의
    # 코드를 소비했으므로 재인증용 코드는 다음 구간에서 새로 받아야 한다.
    reauth_now = frozen_now + timedelta(seconds=31)
    with mfa_clock_frozen(app, reauth_now):
        reauth_code = totp_at(old_secret, reauth_now)
        resetup_response = await client.post(
            "/auth/mfa/setup",
            json={"password": STRONG_PASSWORD, "totp_code": reauth_code},
            headers=headers,
        )
    assert resetup_response.status_code == 200
    new_secret = resetup_response.json()["data"]["secret"]
    assert new_secret != old_secret


async def test_mfa_verify_rejects_invalid_code(client: AsyncClient) -> None:
    """불변식 위반 — 올바른 TOTP 코드가 아닌 값을 보내면 400/401로 거부해야 한다.

    MfaService.verify() 가 유효하지 않은 코드를 받으면 AuthError를 던지고,
    exception_mapping이 401로 매핑한다. 이미 사용된 코드나 잘못된 코드 모두
    거부되어야 한다.
    """
    import pyotp

    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = register_response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # MFA를 먼저 설정하고 verify
    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["data"]["secret"]
    code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    # 이미 사용된 코드는 재사용 불가 — 400/401/500 중 하나로 거부
    verify_again = await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)
    assert verify_again.status_code in (400, 401, 500)

    # 완전히 잘못된 코드도 거부 (FastAPI 검증 → 400, 서비스 검증 → 401)
    wrong_code = await client.post(
        "/auth/mfa/verify", json={"totp_code": "000000"}, headers=headers
    )
    assert wrong_code.status_code in (400, 401)


async def test_mfa_code_from_previous_timestep_is_rejected_at_frozen_clock(
    client: AsyncClient,
) -> None:
    """negative -- esc-ci-cbb8b9c62497 회귀 가드. `MfaService`는 `valid_window=0`
    이라 서버 시계 기준 현재 30초 구간의 코드만 받는다. 코드를 "지금" 만들고
    서버가 다음 구간에서 검증하면(실시간 `pyotp...now()`/`mfa_clock_shifted`가
    경계에서 겪는 레이스) 거부되어야 한다 — 그래서 라우터 테스트는 클라이언트
    코드와 서버 시계를 같은 고정 시각으로 맞춘다."""
    email = _unique_email()
    register_response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {register_response.json()['data']['access_token']}"}
    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["data"]["secret"]

    # Server clock sits 1s past a 30s step boundary; the code was minted 2s earlier
    # (previous step) -- exactly the boundary crossing the race produces.
    base = datetime.now(timezone.utc).replace(microsecond=0)
    server_now = base - timedelta(seconds=base.second % 30) + timedelta(seconds=1)
    stale_code = totp_at(secret, server_now - timedelta(seconds=2))
    with mfa_clock_frozen(app, server_now):
        stale = await client.post(
            "/auth/mfa/verify", json={"totp_code": stale_code}, headers=headers
        )
        # RED_TEAM #11 / FD-11.2: a failed *initial* verify discards the pending
        # secret, so the race does not merely "retry later" -- it silently leaves
        # the account without MFA. Re-run setup, then a clock-aligned code passes.
        resetup_response = await client.post("/auth/mfa/setup", headers=headers)
        fresh_secret = resetup_response.json()["data"]["secret"]
        fresh = await client.post(
            "/auth/mfa/verify",
            json={"totp_code": totp_at(fresh_secret, server_now)},
            headers=headers,
        )
    assert stale.status_code == 400, stale.text
    assert stale.json()["error_code"] == "AUTH_MFA_INVALID"
    assert fresh.status_code == 200, fresh.text
