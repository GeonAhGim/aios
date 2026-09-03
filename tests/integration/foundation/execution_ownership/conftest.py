"""EO-02 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 asyncpg DSN 변환과 `execution_leases`의 FK 대상인
`strategy_executions` 행을 만드는 최소 헬퍼만 둔다(FSM/조건 컴파일은
이 리프 범위 밖 — 리스 저장소는 execution_id 존재만 필요하다)."""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


async def create_execution(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    allocated_capital: Decimal = Decimal("1000"),
) -> int:
    strategy_id = f"lease-test-{uuid.uuid4().hex[:8]}"
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
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated_capital,
        )
    return row["id"]


@pytest.fixture
async def execution_id(pool: asyncpg.Pool) -> int:
    user_id = await create_test_user(pool)
    return await create_execution(pool, user_id)
