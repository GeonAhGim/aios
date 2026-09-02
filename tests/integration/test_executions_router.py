"""16번대 통합테스트 — /executions 라우터. 실제 FastAPI 앱 + 실제 dev DB.

실제 Bitget/KIS Demo 키가 없어 잔고 조회는 FastAPI dependency_overrides로
가짜 CredentialResolver를 주입한다(strategy_builder 라우터 테스트와 동일
패턴)."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_event_bus, get_pool
from src.api.service_deps import get_credential_resolver
from src.data.models.trading import AccountBalance
from src.main import app
from tests.integration.conftest import NoopEventBus

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


class _FakeAdapter:
    async def get_balance(self):
        return [
            AccountBalance(
                exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
            )
        ]


class _FakeResolver:
    async def get_adapter(self, user_id, exchange):
        return _FakeAdapter()


async def _override_resolver(pool=Depends(get_pool)):
    return _FakeResolver()


@pytest.fixture
def event_bus():
    return NoopEventBus()


@pytest.fixture
async def client(event_bus):
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        app.dependency_overrides[get_event_bus] = lambda: event_bus
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_credential_resolver, None)
        app.dependency_overrides.pop(get_event_bus, None)


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


async def _create_approved_strategy(pool, owner_user_id, *, certified_badge=False):
    strategy_id = f"test-strategy-{uuid.uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status, certified_badge)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED', $5)
            """,
            strategy_id,
            version,
            uuid.UUID(owner_user_id),
            json.dumps({}),
            certified_badge,
        )
    return strategy_id, version


async def _link_credential(pool, owner_user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            uuid.UUID(owner_user_id),
            exchange,
            b"dummy",
        )


async def test_create_paper_execution_and_start(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    execution_id = create_response.json()["id"]
    assert create_response.json()["approval_request_id"] is None

    start_response = await client.post(f"/executions/{execution_id}/start", headers=headers)
    assert start_response.status_code == 200
    assert start_response.json()["status"] == "RUNNING"


async def test_create_execution_over_allocation_cap_rejected(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "5000",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )

    assert response.status_code == 400


async def test_live_execution_requires_approval_before_start(client, pool, event_bus):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)

    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "LIVE",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    execution_id = create_response.json()["id"]
    assert create_response.json()["approval_request_id"] is not None
    assert any(topic == "approval.request.created" for topic, _ in event_bus.published)

    start_response = await client.post(f"/executions/{execution_id}/start", headers=headers)
    assert start_response.status_code == 400

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET status = 'APPROVED' "
            "WHERE (context->>'execution_id')::bigint = $1",
            execution_id,
        )

    retry_response = await client.post(f"/executions/{execution_id}/start", headers=headers)
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "RUNNING"


async def test_pause_and_retire_flow(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    execution_id = create_response.json()["id"]
    await client.post(f"/executions/{execution_id}/start", headers=headers)

    pause_response = await client.post(f"/executions/{execution_id}/pause", headers=headers)
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "PAUSED"

    retire_response = await client.post(
        f"/executions/{execution_id}/retire", json={}, headers=headers
    )
    assert retire_response.status_code == 200
    assert retire_response.json()["status"] == "RETIRED"


async def test_list_executions_returns_created(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    execution_id = create_response.json()["id"]

    response = await client.get("/executions", headers=headers)

    assert response.status_code == 200
    assert any(card["execution_id"] == execution_id for card in response.json())


async def test_convert_paper_to_live(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    execution_id = create_response.json()["id"]

    response = await client.post(
        f"/executions/{execution_id}/convert-to-live",
        json={"allocated_capital": "500", "currency": "USDT", "exchange": "bitget"},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["mode"] == "LIVE"
    assert response.json()["approval_request_id"] is not None


async def test_set_and_view_risk_guard(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    execution_id = create_response.json()["id"]

    response = await client.patch(
        f"/executions/{execution_id}/risk-guard",
        json={"max_drawdown_pct": 15},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["max_drawdown_pct"] == "15.00"

    list_response = await client.get("/executions", headers=headers)
    card = next(c for c in list_response.json() if c["execution_id"] == execution_id)
    assert card["max_drawdown_pct"] == "15.00"


async def test_risk_guard_rejects_out_of_range_value(client, pool):
    headers, user_id = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=headers,
    )
    execution_id = create_response.json()["id"]

    response = await client.patch(
        f"/executions/{execution_id}/risk-guard",
        json={"max_drawdown_pct": 200},
        headers=headers,
    )

    assert response.status_code == 400


async def test_risk_guard_rejects_other_users_execution(client, pool):
    owner_headers, owner_id = await _register(client)
    stranger_headers, _ = await _register(client)
    strategy_id, version = await _create_approved_strategy(pool, owner_id)
    await _link_credential(pool, owner_id)
    create_response = await client.post(
        "/executions",
        json={
            "strategy_id": strategy_id,
            "strategy_version": version,
            "allocated_capital": "500",
            "currency": "USDT",
            "exchange": "bitget",
            "mode": "PAPER",
        },
        headers=owner_headers,
    )
    execution_id = create_response.json()["id"]

    response = await client.patch(
        f"/executions/{execution_id}/risk-guard",
        json={"max_drawdown_pct": 15},
        headers=stranger_headers,
    )

    assert response.status_code == 400


async def test_executions_require_authentication(client):
    response = await client.get("/executions")

    assert response.status_code == 401
