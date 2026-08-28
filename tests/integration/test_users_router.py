"""16번대 통합테스트 — /users/me/approval-settings, /users/me/withdrawal-whitelist,
/users/me/delete 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid
from pathlib import Path

import asyncpg
import pyotp
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
async def client():
    # 화이트리스트 등록이 system_safety_state(공유 싱글톤 행)의 circuit_breaker
    # 상태를 확인한다 — 다른 테스트 파일이 남긴 상태에 좌우되지 않도록 리셋.
    conn = await asyncpg.connect(_asyncpg_dsn())
    await conn.execute(
        "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
        "reactivation_approval_id = NULL WHERE id = 1"
    )
    await conn.close()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[str, dict]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    return email, {"Authorization": f"Bearer {token}"}


# ---------- approval settings ----------


async def test_get_approval_settings_defaults_to_solo(client):
    _, headers = await _register(client)

    response = await client.get("/users/me/approval-settings", headers=headers)

    assert response.status_code == 200
    assert response.json()["mode"] == "SOLO"


async def test_update_approval_settings_to_dual(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings",
        json={"mode": "DUAL", "second_approver_contact": "backup@example.com"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "DUAL"


async def test_update_approval_settings_dual_without_contact_rejected(client):
    _, headers = await _register(client)

    response = await client.put(
        "/users/me/approval-settings", json={"mode": "DUAL"}, headers=headers
    )

    assert response.status_code == 400


async def test_approval_settings_requires_authentication(client):
    response = await client.get("/users/me/approval-settings")

    assert response.status_code == 401


# ---------- withdrawal whitelist ----------


async def test_register_whitelist_entry_requires_correct_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "password": "WrongPassword1!",
        },
        headers=headers,
    )

    assert response.status_code == 403


async def test_register_and_list_whitelist_entry(client):
    _, headers = await _register(client)

    register_response = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "label": "콜드월렛",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert register_response.status_code == 201

    list_response = await client.get("/users/me/withdrawal-whitelist", headers=headers)
    assert list_response.status_code == 200
    entries = list_response.json()
    assert any(e["destination_address"] == "bc1qcoldwallet" for e in entries)


async def test_register_whitelist_entry_with_mfa_requires_totp(client):
    email, headers = await _register(client)

    setup_response = await client.post("/auth/mfa/setup", headers=headers)
    secret = setup_response.json()["secret"]
    code = pyotp.totp.TOTP(secret).now()
    await client.post("/auth/mfa/verify", json={"totp_code": code}, headers=headers)

    without_totp = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "password": STRONG_PASSWORD,
        },
        headers=headers,
    )
    assert without_totp.status_code == 403

    login_code = pyotp.totp.TOTP(secret).now()
    with_totp = await client.post(
        "/users/me/withdrawal-whitelist",
        json={
            "exchange": "bitget",
            "destination_address": "bc1qcoldwallet",
            "password": STRONG_PASSWORD,
            "totp_code": login_code,
        },
        headers=headers,
    )
    assert with_totp.status_code == 201


# ---------- account deletion ----------


async def test_request_deletion_succeeds_with_correct_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/delete", json={"password": STRONG_PASSWORD}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_DELETION"


async def test_request_deletion_rejects_wrong_password(client):
    _, headers = await _register(client)

    response = await client.post(
        "/users/me/delete", json={"password": "WrongPassword1!"}, headers=headers
    )

    assert response.status_code == 400


async def test_relogin_after_deletion_request_cancels_it(client):
    email, headers = await _register(client)
    await client.post("/users/me/delete", json={"password": STRONG_PASSWORD}, headers=headers)

    login_response = await client.post(
        "/auth/login", json={"email": email, "password": STRONG_PASSWORD}
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]
    me_response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_response.json()["status"] == "ACTIVE"
