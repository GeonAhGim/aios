"""L23 portfolio_state.py 통합테스트 -- 실 DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L23.

D2: negative >= 3(존재하지 않는 execution_id 조회·저장, 잘못된
expected_version으로 저장, RUNNING 중 저장 거부), 실패 주입 1(DB 커넥션
오류가 삼켜지지 않고 전파), 성능 단언 1(성공 경로 단일 왕복), 게이트
적색 재현 1(status 가드가 빠지면 스위트가 RED가 됨을 먼저 증명). 조립
통합테스트: `PortfolioConfig`(L17)를 실제로 저장·로드해 값이 그대로
왕복함을 확인한다(§9 L23 DoD "조립 통합테스트").
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from dotenv import dotenv_values

import src.services.execution_loop.portfolio_state as store_mod
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.services.execution_loop.portfolio_state import (
    PortfolioConfigLockedError,
    load_config,
    save_config,
)
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


async def _create_execution(pool: asyncpg.Pool, user_id: UUID, *, status: str = "PAUSED") -> int:
    strategy_id = f"portfolio-state-test-{uuid.uuid4().hex[:8]}"
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
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 1000, 'USDT', $3)
            RETURNING id
            """,
            strategy_id,
            user_id,
            status,
        )
    assert row is not None
    return row["id"]


def _config(*, fraction_pct: Decimal = Decimal("50")) -> PortfolioConfig:
    return PortfolioConfig(
        method=SizingMethod.FIXED_FRACTIONAL,
        fraction_pct=fraction_pct,
        min_trade_notional=Decimal("10"),
        cost_model=CostModelRef(model_id="default", cost_model_hash="0" * 64),
    )


async def test_load_missing_config_returns_none(pool):
    """negative -- 저장된 적 없는 실행은 None(존재하지 않음)이지, 기본값
    PortfolioConfig로 위장하지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        result = await load_config(conn, execution_id)

    assert result is None


async def test_load_nonexistent_execution_raises(pool):
    """negative -- 존재하지 않는 execution_id 조회는 조용히 None이 아니라
    LookupError로 실패한다."""
    async with pool.acquire() as conn:
        with pytest.raises(LookupError):
            await load_config(conn, -1)


async def test_save_then_load_round_trips(pool):
    """조립 통합테스트 -- 저장한 PortfolioConfig가 그대로 왕복한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)
    config = _config()

    async with pool.acquire() as conn:
        saved = await save_config(conn, execution_id, config, expected_version=0)
        loaded = await load_config(conn, execution_id)

    assert saved.fraction_pct == Decimal("50")
    assert saved.cost_model.model_id == "default"
    assert loaded is not None
    assert loaded.model_dump() == config.model_dump()


async def test_conditional_update_advances_version(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        await save_config(conn, execution_id, _config(), expected_version=0)
        updated = await save_config(
            conn, execution_id, _config(fraction_pct=Decimal("75")), expected_version=1
        )
        loaded = await load_config(conn, execution_id)

    assert updated.fraction_pct == Decimal("75")
    assert loaded.fraction_pct == Decimal("75")


async def test_stale_expected_version_raises_concurrency_conflict(pool):
    """negative/실패 주입 -- 다른 writer가 이미 version을 앞서 나갔으면
    ConcurrencyConflictError가 삼켜지지 않고 그대로 올라온다(I11)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async with pool.acquire() as conn:
        await save_config(conn, execution_id, _config(), expected_version=0)

        with pytest.raises(ConcurrencyConflictError):
            await save_config(conn, execution_id, _config(), expected_version=0)


async def test_running_execution_rejects_config_change(pool):
    """negative -- §9 L23 DoD "RUNNING 중 config 변경 거부": status=RUNNING
    인 실행은 버전이 맞아도 저장이 거부된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, status="RUNNING")

    async with pool.acquire() as conn:
        with pytest.raises(PortfolioConfigLockedError):
            await save_config(conn, execution_id, _config(), expected_version=0)


async def test_running_rejection_takes_precedence_over_stale_version(pool):
    """negative -- RUNNING 거부는 버전 충돌과 별개의 예외로, 버전이 이미
    stale해도(둘 다 실패 사유일 때) RUNNING 사유가 정확히 보고된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, status="PAUSED")

    async with pool.acquire() as conn:
        await save_config(conn, execution_id, _config(), expected_version=0)
        await conn.execute(
            "UPDATE strategy_executions SET status = 'RUNNING' WHERE id = $1", execution_id
        )

        with pytest.raises(PortfolioConfigLockedError):
            # expected_version=0 is now stale (actual is 1) AND status is
            # RUNNING -- the RUNNING-specific exception must still surface.
            await save_config(conn, execution_id, _config(), expected_version=0)


async def test_connection_failure_propagates_instead_of_being_swallowed(pool, monkeypatch):
    """실패 주입 -- DB 커넥션 오류가 삼켜지지 않고 그대로 전파된다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

    async def raising_fetchrow(self, *args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("simulated connection loss")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", raising_fetchrow)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await save_config(conn, execution_id, _config(), expected_version=0)


async def test_single_round_trip_on_success(pool):
    """성능 단언 -- 성공 경로의 save_config/load_config은 각각 정확히
    1번의 왕복(실패 경로만 상태 조회 1회를 더 쓴다)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id)

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
            await save_config(conn, execution_id, _config(), expected_version=0)
        assert call_count == 1

        call_count = 0
        async with pool.acquire() as conn:
            await load_config(conn, execution_id)
        assert call_count == 1
    finally:
        monkeypatch_target.fetchrow = original_fetchrow


async def test_regression_confirms_suite_would_catch_missing_status_guard(pool, monkeypatch):
    """게이트 적색 재현 -- UPDATE의 `status <> 'RUNNING'` 절이 실수로
    빠지면(결함 주입) 이 스위트가 실제로 RED가 됨을 먼저 증명한 뒤, 원래
    SQL로 되돌려 GREEN을 재확인한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, status="RUNNING")

    original_sql = store_mod._UPDATE_SQL
    assert "AND status <> 'RUNNING'" in original_sql
    broken_sql = original_sql.replace("\n    AND status <> 'RUNNING'", "")
    monkeypatch.setattr(store_mod, "_UPDATE_SQL", broken_sql)

    async with pool.acquire() as conn:
        # 결함 주입 상태 -- 가드가 없으니 RUNNING 실행도 저장이 통과한다(RED 재현).
        broken_save = await save_config(conn, execution_id, _config(), expected_version=0)
    assert broken_save.fraction_pct == Decimal("50")
    monkeypatch.undo()

    async with pool.acquire() as conn:
        with pytest.raises(PortfolioConfigLockedError):
            # 원래 SQL 복구 후: 같은 RUNNING 실행 저장은 이제 거부된다(GREEN 재확인).
            await save_config(conn, execution_id, _config(), expected_version=1)
