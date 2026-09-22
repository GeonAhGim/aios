"""L13 strategy_state_store.py 통합테스트 -- 실 DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §3.7 M1, §9 L13.

D2: negative >= 3(존재하지 않는 execution_id 조회, 잘못된 expected_version으로
저장, 남의 execution_id 행에 대한 저장 시도는 없음 -- FK가 막음을 실증),
실패 주입 1(state_version 경합 시 예외가 삼켜지지 않고 전파), 성능 단언
1(단일 왕복), 게이트 적색 재현 1. D3: 두 커넥션의 실제 동시 첫 저장이
1승 1충돌로 수렴함을 asyncio.gather로 증명(§9 L13 DoD "경합 테스트
1승 1충돌").
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

import src.services.execution_loop.strategy_state_store as store_mod
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.strategy.state_memory import StrategyStateMemory
from src.services.execution_loop.strategy_state_store import load_state, save_state
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def _create_execution(pool: asyncpg.Pool, user_id: UUID) -> int:
    strategy_id = f"state-store-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget',
                    '{}'::jsonb, 'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 1000, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    assert row is not None
    return row["id"]


def _memory(execution_id: int, *, state_version: int = 0) -> StrategyStateMemory:
    return StrategyStateMemory(
        execution_id=execution_id,
        state_version=state_version,
        last_bar_time={"1h": datetime(2026, 1, 1, tzinfo=timezone.utc)},
        prev_values={"RSI_timeperiod14@1h": Decimal("42.5")},
    )


async def test_load_missing_execution_returns_none(pool):
    """negative -- 저장된 적 없는 execution_id는 None(존재하지 않음)이지, 빈
    StrategyStateMemory로 위장하지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        result = await load_state(conn, execution_id)

    assert result is None


async def test_first_save_then_load_round_trips(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    memory = _memory(execution_id)

    async with pool.acquire() as conn:
        saved = await save_state(conn, memory, expected_version=None)
        loaded = await load_state(conn, execution_id)

    assert saved.state_version == 0
    assert saved.last_bar_time == memory.last_bar_time
    assert saved.prev_values == memory.prev_values
    assert loaded is not None
    assert loaded.prev_values == memory.prev_values
    assert loaded.last_bar_time == memory.last_bar_time


async def test_conditional_update_advances_state_version(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        await save_state(conn, _memory(execution_id, state_version=0), expected_version=None)
        advanced = _memory(execution_id, state_version=1)
        advanced = advanced.model_copy(
            update={"prev_values": {"RSI_timeperiod14@1h": Decimal("55")}}
        )
        saved = await save_state(conn, advanced, expected_version=0)
        loaded = await load_state(conn, execution_id)

    assert saved.state_version == 1
    assert loaded.prev_values == {"RSI_timeperiod14@1h": Decimal("55")}


async def test_stale_expected_version_raises_concurrency_conflict(pool, monkeypatch):
    """실패 주입(negative) -- 다른 writer가 이미 state_version을 앞서 나갔으면
    ConcurrencyConflictError가 삼켜지지 않고 그대로 올라온다(I11)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        await save_state(conn, _memory(execution_id, state_version=0), expected_version=None)
        await save_state(conn, _memory(execution_id, state_version=1), expected_version=0)

        with pytest.raises(ConcurrencyConflictError):
            # 이미 state_version=1로 넘어간 행에 여전히 expected_version=0을
            # 주장한다 -- 이 tick은 폐기되어야 한다(I11).
            await save_state(conn, _memory(execution_id, state_version=2), expected_version=0)


async def test_save_for_nonexistent_execution_id_is_rejected_by_fk(pool):
    """negative -- strategy_executions에 없는 execution_id는 FK 위반으로
    거부된다(orphan 상태 행이 만들어지지 않음)."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await save_state(conn, _memory(execution_id=-1), expected_version=None)


async def test_single_round_trip(pool):
    """성능 단언 -- save_state/load_state 각각 정확히 1번의 왕복."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        await save_state(conn, _memory(execution_id), expected_version=None)

    call_count = 0
    original_fetchrow = asyncpg.Connection.fetchrow

    async def counting_fetchrow(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch_target = asyncpg.Connection
    monkeypatch_target.fetchrow = counting_fetchrow
    try:
        async with pool.acquire() as conn:
            await load_state(conn, execution_id)
    finally:
        monkeypatch_target.fetchrow = original_fetchrow

    assert call_count == 1


async def test_concurrent_first_saves_yield_one_win_one_conflict(pool):
    """D3(경합) -- §9 L13 DoD "경합 테스트 1승 1충돌"을 두 개의 실제 커넥션 위
    asyncio.gather로 동시에 실행해 실증한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async def _attempt() -> bool:
        async with pool.acquire() as conn:
            try:
                await save_state(conn, _memory(execution_id), expected_version=None)
                return True
            except ConcurrencyConflictError:
                return False

    results = await asyncio.gather(_attempt(), _attempt())

    assert sorted(results) == [False, True]


async def test_connection_failure_propagates_instead_of_being_swallowed(pool, monkeypatch):
    """실패 주입 -- DB 커넥션 오류가 삼켜지지 않고 그대로 전파된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await save_state(conn, _memory(execution_id), expected_version=None)


async def test_regression_confirms_suite_would_catch_missing_version_guard(pool, monkeypatch):
    """게이트 적색 재현 -- ON CONFLICT의 state_version WHERE 절이 실수로
    빠지면(결함 주입) 이 스위트가 실제로 RED가 됨을 먼저 증명한 뒤, 원래 SQL로
    되돌려 GREEN을 재확인한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    original_sql = store_mod._UPSERT_SQL
    assert "IS NOT DISTINCT FROM $5" in original_sql
    # $5(expected_version)를 계속 참조하면서(asyncpg 인자 개수 불일치를
    # 피하려고) 항상 참이 되는 절로 바꿔 가드만 무력화한다.
    broken_sql = original_sql.replace(
        "WHERE strategy_execution_state.state_version IS NOT DISTINCT FROM $5",
        "WHERE COALESCE($5::int, -1) = COALESCE($5::int, -1)",
    )
    monkeypatch.setattr(store_mod, "_UPSERT_SQL", broken_sql)

    async with pool.acquire() as conn:
        await save_state(conn, _memory(execution_id, state_version=0), expected_version=None)
        # 결함 주입 상태 -- 가드가 없으니 잘못된 expected_version(99)로도 통과한다(RED 재현).
        stale_write = await save_state(
            conn, _memory(execution_id, state_version=1), expected_version=99
        )
    assert stale_write.state_version == 1
    monkeypatch.undo()

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            # 원래 SQL 복구 후: 같은 잘못된 expected_version은 이제 거부된다(GREEN 재확인).
            await save_state(
                conn, _memory(execution_id, state_version=2), expected_version=99
            )
