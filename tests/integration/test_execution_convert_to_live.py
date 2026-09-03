"""16.6 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionControlError, ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
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
def service(pool):
    return ExecutionService(
        pool, load_risk_policy(), pre_start_gate=make_foundation_pre_submit_gate(pool)
    )


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


async def _create_paper_execution(service, pool, user_id):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    return await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )


async def test_convert_creates_new_live_execution_and_preserves_paper(service, pool):
    user_id = await create_test_user(pool)
    paper = await _create_paper_execution(service, pool, user_id)
    await service.start(paper.id, user_id)

    live = await service.convert_to_live(
        user_id,
        paper.id,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        available_balance=Decimal("10000"),
    )

    assert live.id != paper.id
    assert live.mode == "LIVE"
    assert live.approval_request_id is not None

    async with pool.acquire() as conn:
        paper_status = await conn.fetchval(
            "SELECT status FROM strategy_executions WHERE id = $1", paper.id
        )
        linkage = await conn.fetchval(
            "SELECT converted_from_execution_id FROM strategy_executions WHERE id = $1", live.id
        )
    assert paper_status == "RUNNING"  # 종료되지 않고 그대로 이력 보존
    assert linkage == paper.id


async def test_convert_requires_full_approval_flow_again(service, pool):
    user_id = await create_test_user(pool)
    paper = await _create_paper_execution(service, pool, user_id)

    live = await service.convert_to_live(
        user_id,
        paper.id,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        available_balance=Decimal("10000"),
    )

    with pytest.raises(ExecutionControlError):
        await service.start(live.id, user_id)  # 승인 없이는 시작 불가


async def test_cannot_convert_already_live_execution(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    live = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="LIVE",
        available_balance=Decimal("10000"),
    )

    with pytest.raises(ExecutionControlError):
        await service.convert_to_live(
            user_id,
            live.id,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            available_balance=Decimal("10000"),
        )


async def test_convert_rejects_non_owner(service, pool):
    user_id = await create_test_user(pool)
    other_user = await create_test_user(pool)
    paper = await _create_paper_execution(service, pool, user_id)

    with pytest.raises(ExecutionControlError):
        await service.convert_to_live(
            other_user,
            paper.id,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            available_balance=Decimal("10000"),
        )
