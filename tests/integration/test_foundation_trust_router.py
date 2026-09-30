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


# ── negative tests (DoD: negative >= 3) ─────────────────────────────────────


async def test_accept_disclosure_empty_purpose_is_rejected(client):
    """purpose가 빈 문자열이면 매칭되는 disclosure가 없어 404로 거부된다
    (DisclosureNotFoundError -> RESOURCE_NOT_FOUND, accept_disclosure.py)."""
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": "", "disclosure_revision": 1},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "RESOURCE_NOT_FOUND"


async def test_accept_disclosure_missing_revision_is_rejected(client):
    """disclosure_revision 필드가 누락되면 스키마 검증에서 거부된다
    (AcceptDisclosureRequest는 필수 필드, handlers.py의 RequestValidationError
    핸들러는 VALIDATION_INVALID_FIELD -> HTTP 400으로 매핑한다)."""
    headers = await _register(client)
    response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": _unique_purpose()},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "VALIDATION_INVALID_FIELD"


async def test_accept_disclosure_duplicate_purpose_is_rejected(client, pool):
    """동일 tenant가 같은 purpose/revision에 대해 이미 ACTIVE 동의를 가진 상태로
    다시 수락을 시도하면 거부된다(ConsentAlreadyActiveError ->
    STATE_INVALID_TRANSITION -> HTTP 409, accept_disclosure.py)."""
    headers = await _register(client)
    purpose = _unique_purpose()
    await _create_disclosure(pool, purpose)

    first_response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": purpose, "disclosure_revision": 1},
        headers=headers,
    )
    assert first_response.status_code == 201

    dup_response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": purpose, "disclosure_revision": 1},
        headers=headers,
    )
    assert dup_response.status_code == 409
    assert dup_response.json()["error_code"] == "STATE_INVALID_TRANSITION"


# ── failure injection (DoD: >= 1) ───────────────────────────────────────────


async def test_accept_disclosure_repository_failure_returns_internal_error(
    client, pool, monkeypatch
):
    """repository.insert_consent이 매핑되지 않은 예외를 던지면 전역 핸들러가
    INTERNAL_ERROR/500으로 fail-closed 처리해야 한다(handlers.py
    _handle_domain_or_unknown_exception, exception_mapping.map_exception
    fallback)."""
    from src.foundation.trust.adapters.postgres_repository import (
        PostgresTrustRepository,
    )

    async def _raise_on_insert(self, **kwargs):
        raise RuntimeError("repository simulated failure")

    monkeypatch.setattr(PostgresTrustRepository, "insert_consent", _raise_on_insert)

    headers = await _register(client)
    purpose = _unique_purpose()
    await _create_disclosure(pool, purpose)

    response = await client.post(
        "/v1/foundation/trust/consents",
        json={"purpose": purpose, "disclosure_revision": 1},
        headers=headers,
    )
    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_ERROR"
