"""FND-03 통합테스트 — /v1/foundation/evidence/chain:verify 엔드포인트.

실제 FastAPI 앱 + 실제 dev DB. test_foundation_evidence_router.py에서
test_verify_chain_succeeds_for_admin_with_intact_chain를 분리(병렬 실행 격리).
"""

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
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections

STRONG_PASSWORD = "Str0ng!Passw0rd"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await retry_too_many_connections(
        lambda: asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    )
    yield p
    await p.close()


@pytest.fixture
async def client():
    async with lifespan_context_with_retry(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
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
    assert response.json()["data"] == {"verified": True}
