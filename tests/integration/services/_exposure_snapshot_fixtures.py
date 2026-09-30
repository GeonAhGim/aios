"""R-27 exposure_snapshot.py 통합테스트 공용 fixture/helper.

test_exposure_snapshot.py / test_exposure_snapshot_negative.py가 공유하는
DB pool fixture와 행 삽입 헬퍼. 안전 불변식 로직은 없음 — 순수 테스트 배선.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글턴 행 — 다른 테스트 파일의 잔여 상태를
        # 격리한다(test_execution_tick.py와 동일 관례).
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


async def _create_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str) -> int:
    strategy_id = f"exposure-test-{uuid.uuid4().hex[:8]}"
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
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', 1000, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
        )
    assert row is not None
    return row["id"]


async def _insert_position(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    symbol: str,
    exchange: str,
    strategy_id: str,
    quantity: Decimal,
    average_entry_price: Decimal,
    closed_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, quantity, average_entry_price,
                 entry_time, closed_at)
            VALUES ($1, $2, $3, $4, $5, $6, now(), $7)
            """,
            user_id,
            symbol,
            exchange,
            strategy_id,
            quantity,
            average_entry_price,
            closed_at,
        )


async def _insert_order(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    execution_id: int,
    symbol: str,
    exchange: str,
    strategy_id: str,
    created_at: datetime | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders
                (user_id, client_order_id, strategy_id, strategy_version, symbol, exchange,
                 side, order_type, quantity, execution_id, created_at)
            VALUES ($1, $2, $3, '1.0.0', $4, $5, 'BUY', 'MARKET', 1, $6, COALESCE($7, now()))
            """,
            user_id,
            f"order-{uuid.uuid4().hex}",
            strategy_id,
            symbol,
            exchange,
            execution_id,
            created_at,
        )
