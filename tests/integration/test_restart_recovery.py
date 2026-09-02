"""05번 §5.6 재시작 복구 배선 통합테스트 — 실제 Postgres.

전수감사(docs/FULL_AUDIT_2026-09-02.md §3) 회귀: recover_pending_orders는
구현돼 있었으나 호출자가 없었다. 이 테스트는 실제 orders 행을 거래소 재조회
결과로 복구하고 이벤트를 재발행하며 audit_log에 남기는 배선을 검증한다.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.common.adapter import ExchangeAdapter
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.recovery_wiring import (
    RECOVERY_ACTION_TYPE,
    recover_orders_on_startup,
)
from src.services.order_service import repository
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


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


async def _insert_submitted_order(
    pool: asyncpg.Pool, user_id: uuid.UUID, execution_id: int
) -> Order:
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT strategy_id, strategy_version FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        order = Order(
            client_order_id=f"recovery-{uuid.uuid4().hex}",
            exchange_order_id=f"ex-{uuid.uuid4().hex[:12]}",
            strategy_id=execution["strategy_id"],
            strategy_version=execution["strategy_version"],
            execution_id=execution_id,
            symbol="BTC/USDT",
            exchange="bitget",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            status=OrderStatus.SUBMITTED,
            asset_class=AssetClass.CRYPTO,
        )
        return await repository.insert(conn, order, user_id=user_id)


def _resolver_for(adapters: dict[uuid.UUID, ExchangeAdapter]):
    async def resolve(user_id: uuid.UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError("자격증명 없음(테스트 리졸버)") from exc

    return resolve


async def _order_status(pool: asyncpg.Pool, order_id: uuid.UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)


async def test_recovery_persists_cancelled_status_and_republishes(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.CANCELLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    recovered = await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert recovered >= 1
    assert await _order_status(pool, order.order_id) == "CANCELLED"
    mine = [
        p
        for t, p in published
        if t == "order.status.changed" and p["order_id"] == str(order.order_id)
    ]
    assert len(mine) == 1
    assert mine[0]["status"] == "CANCELLED"
    assert mine[0]["recovered"] is True
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE action_type = $1", RECOVERY_ACTION_TYPE
        )
    assert rows
    assert any("recovered_orders" in json.loads(r["decision_data"]) for r in rows)


async def test_recovery_leaves_filled_order_for_the_tick_to_apply(pool):
    """FILLED는 tick의 apply_fill + FSM 전이 경로가 유일한 반영 지점이다 —
    복구가 먼저 FILLED로 쓰면 실행이 PENDING에 갇힌다."""
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    adapter = FakeExchangeAdapter(get_order_status=OrderStatus.FILLED)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(
        pool, resolve_adapter=_resolver_for({user: adapter}), publish=publish
    )

    assert await _order_status(pool, order.order_id) == "SUBMITTED"
    mine = [p for t, p in published if p.get("order_id") == str(order.order_id)]
    assert len(mine) == 1
    assert mine[0]["status"] == "SUBMITTED"
    assert mine[0]["exchange_status"] == "FILLED"


async def test_recovery_skips_orders_without_credentials(pool):
    user = await create_test_user(pool)
    execution_id = await _create_execution(pool, user)
    order = await _insert_submitted_order(pool, user, execution_id)
    published: list[tuple[str, dict]] = []

    async def publish(topic: str, payload: dict) -> None:
        published.append((topic, payload))

    await recover_orders_on_startup(pool, resolve_adapter=_resolver_for({}), publish=publish)

    assert await _order_status(pool, order.order_id) == "SUBMITTED"
    assert not [p for _, p in published if p.get("order_id") == str(order.order_id)]
