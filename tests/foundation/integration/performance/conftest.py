"""L48/L49 통합테스트 공용 픽스처 — pool + 최소 strategy_executions/orders/
positions/reconciliation_state 삽입 헬퍼(tests/integration/test_metrics_collector.py의
`_create_running_execution` 패턴을 이 디렉터리로 옮겨온다).

task-7950 DEEPEN: 위 헬퍼들은 실DB에 직접 쓰는 fail-closed 경로다 — 불변식
위반 입력(FK 없는 tenant, CHECK 밖 aggregate_status, 중복 UNIQUE 키)이
조용히 통과하지 않고 asyncpg 예외로 거부되는지, 그리고 pool 획득 실패가
삼켜지지 않고 그대로 전파되는지를 아래 테스트로 고정한다."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
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
    assert row is not None
    return int(row["id"])


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


async def test_set_reconciliation_state_rejects_unknown_aggregate_status(
    pool: asyncpg.Pool,
) -> None:
    """reconciliation_state.aggregate_status는 f2b8e5d1a734 마이그레이션의
    CHECK(aggregate_status IN (...)) 밖 값을 거부한다 — 집계 상태 오타가
    조용히 저장되면 재조정 게이트가 잘못된 상태를 건강으로 취급한다."""
    from tests.integration.conftest import create_test_tenant

    user_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        await set_reconciliation_state(pool, user_id, aggregate_status="NOT_A_REAL_STATUS")


async def test_set_reconciliation_state_rejects_unknown_tenant(pool: asyncpg.Pool) -> None:
    """reconciliation_state.tenant_id는 users(user_id)를 FK 참조한다(f2b8e5d1a734) —
    존재하지 않는 tenant로는 재조정 상태를 만들 수 없다."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await set_reconciliation_state(pool, uuid4(), aggregate_status="HEALTHY")


async def test_insert_filled_order_rejects_unknown_execution(pool: asyncpg.Pool) -> None:
    """orders.execution_id는 strategy_executions(id)를 FK 참조한다 — 존재하지
    않는 실행에 체결을 붙이면 포지션/원장 계산이 고아 체결을 조용히 집계한다."""
    from tests.integration.conftest import create_test_tenant

    user_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await insert_filled_order(pool, user_id, execution_id=999_999_999)


async def test_insert_position_rejects_duplicate_entry_time(pool: asyncpg.Pool) -> None:
    """positions는 UNIQUE(symbol, exchange, strategy_id, entry_time)다(c8ead41fd624) —
    같은 진입 시각으로 같은 포지션을 두 번 기록하면 이중 계상이 된다."""
    from tests.integration.conftest import create_test_tenant

    user_id = await create_test_tenant(pool)
    execution_id = await create_paper_execution(pool, user_id)
    entry_time = datetime.now(timezone.utc)
    await insert_position(pool, user_id, execution_id, entry_time=entry_time)
    with pytest.raises(asyncpg.UniqueViolationError):
        await insert_position(pool, user_id, execution_id, entry_time=entry_time)


async def test_create_paper_execution_propagates_pool_acquire_failure(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """의존성 실패 주입 — pool.acquire()가 예외를 내면 create_paper_execution은
    그 예외를 삼키지 않고 그대로 전파해야 한다(fail-closed, 105번 표준)."""
    from tests.integration.conftest import create_test_tenant

    user_id = await create_test_tenant(pool)

    def _broken_acquire(self: asyncpg.Pool, *args: object, **kwargs: object) -> None:
        raise ConnectionError("simulated pool exhaustion")

    monkeypatch.setattr(type(pool), "acquire", _broken_acquire)
    with pytest.raises(ConnectionError, match="simulated pool exhaustion"):
        await create_paper_execution(pool, user_id)
