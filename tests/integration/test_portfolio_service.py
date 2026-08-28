"""19.1 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionService
from src.services.portfolio_service import PortfolioService
from tests.integration.conftest import create_test_user


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


@pytest.fixture
def execution_service(pool):
    return ExecutionService(pool, load_risk_policy())


@pytest.fixture
def portfolio_service(pool):
    return PortfolioService(pool)


async def _create_approved_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _link_credential(pool, user_id):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, 'bitget', $2, $2)",
            user_id,
            b"dummy",
        )


async def _create_running_execution(
    execution_service, pool, user_id, *, capital=Decimal("1000"), link_credential=True
):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=capital,
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("100000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id


async def test_no_executions_shows_all_cash(portfolio_service, pool):
    user_id = await create_test_user(pool)

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("5000"))

    assert view.allocations == []
    assert view.unallocated_cash == Decimal("5000")
    assert view.unallocated_cash_weight_pct == Decimal("100")


async def test_three_executions_weights_sum_to_100_percent(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    await _create_running_execution(execution_service, pool, user_id, capital=Decimal("1000"))
    await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("2000"), link_credential=False
    )
    await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("3000"), link_credential=False
    )

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    total_weight = view.unallocated_cash_weight_pct + sum(
        a.weight_pct for a in view.allocations
    )
    assert round(total_weight, 6) == Decimal("100")
    assert len(view.allocations) == 3


async def test_retired_execution_excluded_from_portfolio(
    execution_service, portfolio_service, pool
):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.retire(execution_id, user_id)

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    assert all(a.execution_id != execution_id for a in view.allocations)


async def test_pnl_included_in_current_value(execution_service, portfolio_service, pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_running_execution(
        execution_service, pool, user_id, capital=Decimal("1000")
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget',
                    (SELECT strategy_id FROM strategy_executions WHERE id = $2),
                    $2, 1.0, 50000, 150, 0, now())
            """,
            user_id,
            execution_id,
        )

    view = await portfolio_service.get_portfolio(user_id, total_cash_balance=Decimal("10000"))

    allocation = next(a for a in view.allocations if a.execution_id == execution_id)
    assert allocation.current_value == Decimal("1150")
