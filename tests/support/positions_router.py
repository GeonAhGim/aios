"""LB-19 positions router: API fixtures and position seeding."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.data.models.base import Currency, Money
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    PositionSnapshotView,
)
from src.foundation.positions.domain.position_key import PositionKey
from src.main import app
from tests.conftest import lifespan_context_with_retry, retry_too_many_connections
from tests.support.entities_seed import bootstrap_default_portfolio

STRONG_PASSWORD = "Str0ng!Passw0rd"



BASE = "/v1/positions"



def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")



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
        # raise_app_exceptions=False — 도메인 예외는 전역 핸들러가 봉투로 번역하고
        # Starlette가 정상 응답 뒤에도 재전파하므로(test_auth_router.py 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac



async def _register(client: AsyncClient) -> tuple[dict, UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return headers, UUID(me.json()["data"]["user_id"])



async def _create_account(pool: asyncpg.Pool, tenant_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        account_id: UUID = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            tenant_id,
            f"V{uuid.uuid4().hex[:8]}",
            Currency.KRW.value,
            CostMethod.FIFO.value,
        )
    return account_id



async def _open_position(
    pool: asyncpg.Pool,
    *,
    tenant_id: UUID,
    account_id: UUID,
    quantity: Decimal,
    portfolio_id: UUID | None = None,
) -> PositionSnapshotView:
    # FA-0d-fix: the adapter now requires a 5-part key whose portfolio the
    # tenant owns -- callers that do not pick a portfolio get the tenant's
    # FA-1 default one. The default hierarchy is bootstrapped (idempotently,
    # API-registered tenants have none yet) even when an explicit portfolio is
    # given: a migration round trip below FA-4 re-derives `pos_snapshot.
    # portfolio_id` from the tenant default, and FA-0d fails closed on rows
    # whose tenant has none.
    default_portfolio_id = await bootstrap_default_portfolio(pool, tenant_id)
    if portfolio_id is None:
        portfolio_id = default_portfolio_id
    key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=uuid.uuid4().hex,
            strategy_id="strat",
            execution_id="exec",
            portfolio_id=portfolio_id,
        )
    )
    snapshot = PositionSnapshotView(
        position_key=key,
        tenant_id=tenant_id,
        account_id=account_id,
        instrument_id=uuid.uuid4(),
        quantity=quantity,
        avg_cost=Money(amount=Decimal("100"), currency=Currency.KRW),
        cost_method=CostMethod.FIFO,
        lots=[],
        realized_pnl_base=Decimal("0"),
        unrealized_pnl_base=None,
        fees_base=Decimal("0"),
        funding_base=Decimal("0"),
        mark_price=None,
        mark_at=None,
        base_currency=Currency.KRW,
        last_journal_seq=0,
        updated_at=datetime.now(timezone.utc),
    )
    repo = PostgresSnapshotRepository(pool)
    async with pool.acquire() as conn, conn.transaction():
        return await repo.upsert(conn, snapshot, expected_seq=0)



def _assert_error_envelope(body: dict, code: str) -> None:
    assert body["error_code"] == code
    assert set(body) >= {"error_code", "message", "trace_id"}
    assert "data" not in body
