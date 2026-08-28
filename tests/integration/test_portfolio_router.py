"""19번대 통합테스트 — /portfolio 라우터. 실제 FastAPI 앱 + 실제 dev DB.

실제 Bitget Demo 키가 없어 FastAPI dependency_overrides로 가짜
adapter_factory/resolver를 주입한다(exchange_credentials 라우터 테스트와
동일 패턴)."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_pool
from src.api.service_deps import get_credential_resolver, get_exchange_credential_service
from src.exchanges.common.types import ExchangeCapability
from src.main import app
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService

STRONG_PASSWORD = "Str0ng!Passw0rd"
ENCRYPTION_KEY = "44" * 32


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
    def __init__(self, api_key, api_secret, extra):
        self.api_key = api_key

    async def get_balance(self):
        return [
            _balance("USDT", Decimal("10000")),
        ]

    def get_capabilities(self):
        return ExchangeCapability(
            exchange_name="bitget",
            supported_asset_classes=[],
            supports_spot=True,
            supports_futures=False,
            supports_leverage=False,
            supports_websocket=True,
            max_leverage=1,
            reference_feed_coverage="high",
            has_official_sandbox=True,
        )

    async def aclose(self):
        pass


def _balance(asset, available):
    from src.data.models.trading import AccountBalance

    return AccountBalance(exchange="bitget", asset=asset, total=available, available=available)


def _fake_factory(exchange, api_key, api_secret, extra, *, demo_mode=True):
    return _FakeAdapter(api_key, api_secret, extra)


async def _override_credential_service(pool=Depends(get_pool)):
    return ExchangeCredentialService(
        pool, encryption_key=ENCRYPTION_KEY, adapter_factory=_fake_factory
    )


async def _override_resolver(pool=Depends(get_pool)):
    service = ExchangeCredentialService(
        pool, encryption_key=ENCRYPTION_KEY, adapter_factory=_fake_factory
    )
    return CredentialResolver(service, adapter_factory=_fake_factory)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_exchange_credential_service] = _override_credential_service
        app.dependency_overrides[get_credential_resolver] = _override_resolver
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_exchange_credential_service, None)
        app.dependency_overrides.pop(get_credential_resolver, None)


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


async def _link_credential(client, headers):
    await client.post(
        "/exchange-credentials",
        json={"exchange": "bitget", "api_key": "good-key", "api_secret": "secret"},
        headers=headers,
    )


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


async def test_portfolio_with_no_executions_is_all_unallocated_cash(client):
    headers, _ = await _register(client)
    await _link_credential(client, headers)

    response = await client.get("/portfolio", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["allocations"] == []
    assert Decimal(body["unallocated_cash"]) == Decimal("10000")
    assert Decimal(body["unallocated_cash_weight_pct"]) == Decimal("100")


async def test_portfolio_reflects_running_execution(client, pool):
    headers, user_id = await _register(client)
    await _link_credential(client, headers)
    strategy_id, version = await _create_approved_strategy(pool, user_id)

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

    response = await client.get("/portfolio", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["allocations"]) == 1
    assert body["allocations"][0]["execution_id"] == execution_id
    weights = [Decimal(a["weight_pct"]) for a in body["allocations"]]
    weights.append(Decimal(body["unallocated_cash_weight_pct"]))
    assert sum(weights) == Decimal("100")


async def test_rebalance_decrease_needs_no_approval(client, pool):
    headers, user_id = await _register(client)
    await _link_credential(client, headers)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
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

    response = await client.post(
        "/portfolio/rebalance",
        json={"adjustments": [{"execution_id": execution_id, "new_allocated_capital": "200"}]},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["adjusted"] == 1
    assert body["pending_approval"] == 0


async def test_rebalance_over_cash_balance_rejected(client, pool):
    headers, user_id = await _register(client)
    await _link_credential(client, headers)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
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

    response = await client.post(
        "/portfolio/rebalance",
        json={
            "adjustments": [
                {"execution_id": execution_id, "new_allocated_capital": "999999"}
            ]
        },
        headers=headers,
    )

    assert response.status_code == 400


async def test_portfolio_requires_authentication(client):
    response = await client.get("/portfolio")

    assert response.status_code == 401
