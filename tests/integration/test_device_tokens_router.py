"""21번대 통합테스트 — /device-tokens 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_register_device_token(client):
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["is_active"] is True


async def test_register_rejects_unknown_platform(client):
    headers = await _register(client)

    response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "WindowsPhone"},
        headers=headers,
    )

    assert response.status_code == 400


async def test_deactivate_own_device(client):
    headers = await _register(client)
    register_response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "Android"},
        headers=headers,
    )
    device_id = register_response.json()["device_id"]

    response = await client.delete(f"/device-tokens/{device_id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "deactivated"


async def test_cannot_deactivate_other_users_device(client):
    owner_headers = await _register(client)
    stranger_headers = await _register(client)
    register_response = await client.post(
        "/device-tokens",
        json={"device_token": "token-abc", "platform": "iOS"},
        headers=owner_headers,
    )
    device_id = register_response.json()["device_id"]

    response = await client.delete(f"/device-tokens/{device_id}", headers=stranger_headers)

    assert response.status_code == 404


async def test_device_tokens_require_authentication(client):
    response = await client.post(
        "/device-tokens", json={"device_token": "token-abc", "platform": "iOS"}
    )

    assert response.status_code == 401
