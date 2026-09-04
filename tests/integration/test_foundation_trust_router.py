"""FND-01 통합테스트 — /v1/foundation/trust 라우터. 실제 FastAPI 앱 + 실제 dev DB."""
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
        # raise_app_exceptions=False — task-1218이 trust.py의 raw HTTPException을
        # 도메인 예외로 교체했다(이유는 tests/integration/test_auth_router.py의
        # client 픽스처와 동일).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


def _unique_purpose() -> str:
    return f"test-purpose-{uuid.uuid4().hex[:8]}"


async def _register(client) -> dict:
    email = _unique_email()
    response = await client.post(
        "/auth/register", json={"email": email, "password": STRONG_PASSWORD}
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_disclosure(pool: asyncpg.Pool, purpose: str) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO disclosure (purpose, revision, content_hash) "
            "VALUES ($1, 1, 'hash') RETURNING id",
            purpose,
        )
    return str(row["id"])


async def test_get_trust_status_requires_authentication(client):
    response = await client.get("/v1/foundation/trust/status")
    assert response.status_code == 401


async def test_get_trust_status_starts_empty(client):
    headers = await _register(client)
    response = await client.get("/v1/foundation/trust/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["consents"] == []


async def test_accept_disclosure_then_appears_in_status(client, pool):
    headers = await _register(client)
    purpose = _unique_purpose()
    await _create_disclosure(pool, purpose)

    accept_response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": purpose, "disclosure_revision": 1},
        headers=headers,
    )
    assert accept_response.status_code == 201
    consent_id = accept_response.json()["data"]["consent_id"]

    status_response = await client.get("/v1/foundation/trust/status", headers=headers)
    purposes = [c["purpose"] for c in status_response.json()["data"]["consents"]]
    assert purpose in purposes

    revoke_response = await client.post(
        f"/v1/foundation/trust/consents/{consent_id}:revoke", headers=headers
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["data"]["state"] == "REVOKED"

    status_after = await client.get("/v1/foundation/trust/status", headers=headers)
    purposes_after = [c["purpose"] for c in status_after.json()["data"]["consents"]]
    assert purpose not in purposes_after


async def test_accept_disclosure_for_unknown_purpose_is_404(client):
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": "no-such-purpose", "disclosure_revision": 1},
        headers=headers,
    )
    assert response.status_code == 404


async def test_cannot_revoke_another_users_consent_via_api(client, pool):
    owner_headers = await _register(client)
    attacker_headers = await _register(client)
    purpose = _unique_purpose()
    await _create_disclosure(pool, purpose)

    accept_response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": purpose, "disclosure_revision": 1},
        headers=owner_headers,
    )
    consent_id = accept_response.json()["data"]["consent_id"]

    attack_response = await client.post(
        f"/v1/foundation/trust/consents/{consent_id}:revoke", headers=attacker_headers
    )
    assert attack_response.status_code == 403
