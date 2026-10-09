"""공유 fixture/헬퍼 — open_order_sweeper 테스트 분할(test_open_order_sweeper*.py)
전용. 실 Postgres(TEST_DATABASE_URL/DATABASE_URL) 대상 통합테스트 지원 코드다.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.exchanges.common.adapter import ExchangeAdapter
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


class _RaisingCancelAdapter(FakeExchangeAdapter):
    """cancel_order가 항상 예외를 던지는 대역 — 어댑터 부분 실패 테스트 전용."""

    def __init__(self, *, exchange_name: str = "bitget") -> None:
        super().__init__(exchange_name=exchange_name)
        self.cancel_call_count = 0

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_call_count += 1
        raise RuntimeError("exchange unreachable")


class _CountingCancelAdapter(FakeExchangeAdapter):
    """cancel_order 호출 횟수를 세는 대역 — 멱등성 테스트 전용."""

    def __init__(self, *, exchange_name: str = "bitget") -> None:
        super().__init__(exchange_name=exchange_name)
        self.cancel_call_count = 0

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_call_count += 1
        return True


async def _seed_order(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    exchange: str = "bitget",
    status: str = "SUBMITTED",
    execution_id: int | None = None,
) -> UUID:
    exchange_order_id = f"ex-{uuid.uuid4().hex[:12]}" if status != "CREATED" else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id,
                strategy_version, execution_id, symbol, exchange, side, order_type,
                quantity, status
            ) VALUES (
                $1, $2, $3, 'sweeper-test', '1.0.0', $4, 'BTC/USDT', $5, 'BUY',
                'LIMIT', 1.0, $6
            )
            RETURNING order_id
            """,
            user_id,
            f"sweeper-{uuid.uuid4().hex}",
            exchange_order_id,
            execution_id,
            exchange,
            status,
        )
    return row["order_id"]


async def _seed_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str = "bitget") -> int:
    strategy_id = f"sweeper-exec-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
        )
    return row["id"]


async def _status_of(pool: asyncpg.Pool, order_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert row is not None
    return row["status"]


def _adapters(*names: str) -> dict[str, ExchangeAdapter]:
    return {name: FakeExchangeAdapter(exchange_name=name) for name in names}
