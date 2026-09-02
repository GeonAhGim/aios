"""FD-16(신설) 통합테스트 — 실행별 손실 한도 자동 정지, 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionControlError, ExecutionService
from src.services.risk_guard_service import RiskGuardService
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
def risk_guard(pool, execution_service):
    return RiskGuardService(pool, execution_service)


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


async def _create_running_execution(execution_service, pool, user_id):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("1000"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id, strategy_id


async def _insert_position(
    pool, user_id, execution_id, strategy_id, *, realized=Decimal("0"), unrealized=Decimal("0")
):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'BTC/USDT', 'bitget', $2, $3, 1.0, 50000, $4, $5, now())
            """,
            user_id,
            strategy_id,
            execution_id,
            unrealized,
            realized,
        )


async def test_evaluate_pauses_execution_exceeding_drawdown_limit(
    execution_service, risk_guard, pool
):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.set_max_drawdown(execution_id, user_id, Decimal("10"))
    # 1000 배분에 -150 손실 = -15% > 10% 한도
    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("-150"), unrealized=Decimal("0")
    )

    paused = await risk_guard.evaluate_all_running()

    assert execution_id in paused
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["status"] == "PAUSED"
    assert row["paused_by"] == "SAFETY_LAYER"


async def test_evaluate_leaves_execution_within_limit_running(
    execution_service, risk_guard, pool
):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.set_max_drawdown(execution_id, user_id, Decimal("10"))
    # 1000 배분에 -50 손실 = -5% < 10% 한도
    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("-50"), unrealized=Decimal("0")
    )

    paused = await risk_guard.evaluate_all_running()

    assert execution_id not in paused
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_executions WHERE id = $1", execution_id
        )
    assert status == "RUNNING"


async def test_evaluate_ignores_execution_without_guard_set(execution_service, risk_guard, pool):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)
    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("-900"), unrealized=Decimal("0")
    )

    paused = await risk_guard.evaluate_all_running()

    assert execution_id not in paused


async def test_evaluate_publishes_safety_block_event(execution_service, pool):
    published = []

    async def _publish(topic, payload):
        published.append((topic, payload))

    risk_guard = RiskGuardService(pool, execution_service, publish=_publish)
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.set_max_drawdown(execution_id, user_id, Decimal("10"))
    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("-150"), unrealized=Decimal("0")
    )

    await risk_guard.evaluate_all_running()

    assert any(
        topic == "execution.safety_block.applied" and payload["execution_id"] == execution_id
        for topic, payload in published
    )


async def test_set_max_drawdown_rejects_out_of_range(execution_service, pool):
    user_id = await create_test_user(pool)
    execution_id, _ = await _create_running_execution(execution_service, pool, user_id)

    with pytest.raises(ExecutionControlError):
        await execution_service.set_max_drawdown(execution_id, user_id, Decimal("150"))


async def test_set_max_drawdown_rejects_other_users_execution(execution_service, pool):
    owner = await create_test_user(pool)
    stranger = await create_test_user(pool)
    execution_id, _ = await _create_running_execution(execution_service, pool, owner)

    with pytest.raises(ExecutionControlError):
        await execution_service.set_max_drawdown(execution_id, stranger, Decimal("10"))


async def test_set_max_drawdown_none_clears_guard(execution_service, risk_guard, pool):
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)
    await execution_service.set_max_drawdown(execution_id, user_id, Decimal("10"))
    await execution_service.set_max_drawdown(execution_id, user_id, None)
    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("-900"), unrealized=Decimal("0")
    )

    paused = await risk_guard.evaluate_all_running()

    assert execution_id not in paused
