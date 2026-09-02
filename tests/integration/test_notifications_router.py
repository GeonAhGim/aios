"""17번대 통합테스트 — /notifications 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
import uuid
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
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


async def _register(client) -> tuple[dict, str]:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["data"]["user_id"]


async def _insert_notification(pool, user_id, event_type="marketplace.purchase.requested"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO notifications (user_id, event_type, channel, status) "
            "VALUES ($1, $2, 'EMAIL', 'SENT')",
            uuid.UUID(user_id),
            event_type,
        )


async def test_history_empty_by_default(client):
    headers, _ = await _register(client)

    response = await client.get("/notifications/history", headers=headers)

    assert response.status_code == 200
    assert response.json() == []


async def test_history_returns_inserted_notification(client, pool):
    headers, user_id = await _register(client)
    await _insert_notification(pool, user_id)

    response = await client.get("/notifications/history", headers=headers)

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["event_type"] == "marketplace.purchase.requested"


async def test_history_filters_by_event_type(client, pool):
    headers, user_id = await _register(client)
    await _insert_notification(pool, user_id, event_type="risk_profile.match.warned")
    await _insert_notification(pool, user_id, event_type="marketplace.purchase.requested")

    response = await client.get(
        "/notifications/history",
        params={"event_type": "risk_profile.match.warned"},
        headers=headers,
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["event_type"] == "risk_profile.match.warned"


async def test_get_preferences_defaults_to_all_true(client):
    headers, _ = await _register(client)

    response = await client.get("/notifications/preferences", headers=headers)

    assert response.status_code == 200
    assert all(v is True for v in response.json().values())


async def test_update_preferences_applies_allowed_and_rejects_unknown(client):
    headers, _ = await _register(client)

    response = await client.put(
        "/notifications/preferences",
        json={"marketplace_purchase_email": False, "unknown_field": True},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"]["marketplace_purchase_email"] is False
    assert body["rejected_fields"] == ["unknown_field"]


async def test_notifications_require_authentication(client):
    response = await client.get("/notifications/history")

    assert response.status_code == 401
