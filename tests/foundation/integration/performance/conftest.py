"""L48/L49 통합테스트 공용 픽스처 — pool + 최소 strategy_executions/orders/
positions/reconciliation_state 삽입 헬퍼(tests/integration/test_metrics_collector.py의
`_create_running_execution` 패턴을 이 디렉터리로 옮겨온다)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def create_paper_execution(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    allocated_capital: Decimal = Decimal("1000"),
    started_at: datetime | None = None,
) -> int:
    strategy_id = f"perf-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status, started_at)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING', $4)
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated_capital,
            started_at,
        )
    return row["id"]


async def insert_filled_order(
    pool: asyncpg.Pool,
    user_id: UUID,
    execution_id: int,
    *,
    average_fill_price: Decimal = Decimal("100"),
    filled_quantity: Decimal = Decimal("1"),
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                order_id, user_id, client_order_id, strategy_id, strategy_version,
                execution_id, symbol, exchange, side, order_type, quantity, status,
                filled_quantity, average_fill_price, is_liquidation
            ) VALUES (
                gen_random_uuid(), $1, $2, 'strat-1', '1.0.0', $3, 'BTC/USDT', 'bitget',
                'BUY', 'MARKET', $4, 'FILLED', $4, $5, false
            )
            """,
            user_id,
            f"perf-order-{uuid.uuid4().hex}",
            execution_id,
            filled_quantity,
            average_fill_price,
        )


async def insert_position(
    pool: asyncpg.Pool,
    user_id: UUID,
    execution_id: int,
    *,
    entry_time: datetime,
    quantity: Decimal = Decimal("1"),
    average_entry_price: Decimal = Decimal("100"),
    unrealized_pnl: Decimal = Decimal("5"),
    realized_pnl: Decimal = Decimal("0"),
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions (
                user_id, symbol, exchange, strategy_id, execution_id, quantity,
                average_entry_price, unrealized_pnl, realized_pnl, entry_time
            ) VALUES ($1, 'BTC/USDT', 'bitget', 'strat-1', $2, $3, $4, $5, $6, $7)
            """,
            user_id,
            execution_id,
            quantity,
            average_entry_price,
            unrealized_pnl,
            realized_pnl,
            entry_time,
        )


async def set_reconciliation_state(
    pool: asyncpg.Pool, user_id: UUID, *, aggregate_status: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO reconciliation_state
                (target_ref, target_type, tenant_id, aggregate_status, last_checked_at, revision)
            VALUES ($1, 'paper_account', $1, $2, now(), 0)
            ON CONFLICT (target_ref) DO UPDATE SET aggregate_status = EXCLUDED.aggregate_status
            """,
            user_id,
            aggregate_status,
        )
