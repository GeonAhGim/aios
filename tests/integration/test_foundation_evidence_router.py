"""FND-03 통합테스트 — /v1/foundation/evidence 라우터. 실제 FastAPI 앱 + 실제
dev DB. 쓰기 API는 없다(스키마 docstring 참조) — 이 라우터는 읽기 전용이라
직접 이벤트를 만들려면 application 계층(append_audit_event)을 통해야 한다."""
import uuid
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values
from httpx import ASGITransport, AsyncClient

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.contracts.v1 import Outcome, RecordAuditEventCommand
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
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/users/me", headers=headers)
    return headers, me.json()["user_id"]


async def test_timeline_requires_authentication(client):
    response = await client.get("/v1/foundation/evidence/timeline")
    assert response.status_code == 401


async def test_timeline_starts_empty(client):
    headers, _ = await _register(client)
    response = await client.get("/v1/foundation/evidence/timeline", headers=headers)
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_timeline_shows_own_events_only(client, pool):
    owner_headers, owner_id = await _register(client)
    _, attacker_id = await _register(client)
    repo = PostgresAuditEventRepository(pool)

    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=uuid.UUID(owner_id),
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid.UUID(owner_id),
            trace_id=uuid4(),
            payload={"note": "test"},
        ),
    )
    del attacker_id

    response = await client.get("/v1/foundation/evidence/timeline", headers=owner_headers)
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "mandate_activated"


async def test_verify_chain_requires_admin(client):
    headers, _ = await _register(client)
    response = await client.post("/v1/foundation/evidence/chain:verify", headers=headers)
    assert response.status_code == 403


async def test_verify_chain_succeeds_for_admin_with_intact_chain(client, pool):
    headers, user_id = await _register(client)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE user_id = $1", uuid.UUID(user_id)
        )

    repo = PostgresAuditEventRepository(pool)
    await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=uuid.UUID(user_id),
            aggregate_type="mandate_revision",
            aggregate_id=uuid4(),
            action="mandate_activated",
            outcome=Outcome.SUCCESS,
            actor_subject_id=uuid.UUID(user_id),
            trace_id=uuid4(),
            payload={},
        ),
    )

    response = await client.post(
        "/v1/foundation/evidence/chain:verify",
        params={"tenant_id": user_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"verified": True}
