"""16.2 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.capital_allocation import CapitalAllocationError
from src.services.execution_service import ExecutionCreateError, ExecutionService
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


async def _create_approved_strategy(pool, owner_user_id, *, certified_badge=False):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status, certified_badge)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author',
                    'APPROVED', $5)
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
            certified_badge,
        )
    return strategy_id, version


async def _link_credential(pool, user_id, exchange="bitget"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exchange_credentials "
            "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
            "VALUES ($1, $2, $3, $3)",
            user_id,
            exchange,
            b"dummy",
        )


async def test_create_paper_execution_needs_no_approval(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    result = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )

    assert result.status == "PENDING_APPROVAL"
    assert result.approval_request_id is None


async def test_create_live_execution_creates_approval_request(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)

    result = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="LIVE",
        available_balance=Decimal("10000"),
    )

    assert result.approval_request_id is not None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT context, requested_action FROM approval_requests WHERE id = $1",
            result.approval_request_id,
        )
    assert row["requested_action"] == "START_LIVE_EXECUTION"
    assert json.loads(row["context"])["execution_id"] == result.id


async def test_kis_live_rejected(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id, exchange="kis")

    with pytest.raises(ExecutionCreateError):
        await service.create_execution(
            user_id,
            strategy_id,
            version,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="kis",
            mode="LIVE",
            available_balance=Decimal("10000"),
        )


async def test_kis_paper_allowed(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id, exchange="kis")

    result = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="KRW",
        exchange="kis",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )
    assert result.exchange == "kis"


async def test_rejects_strategy_not_yet_approved(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb, 'test-author')
            """,
            strategy_id,
            owner,
            json.dumps({}),
        )
    await _link_credential(pool, owner)

    with pytest.raises(ExecutionCreateError):
        await service.create_execution(
            owner,
            strategy_id,
            "1.0.0",
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            mode="PAPER",
            available_balance=Decimal("10000"),
        )


async def test_rejects_unlinked_exchange(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)

    with pytest.raises(ExecutionCreateError):
        await service.create_execution(
            user_id,
            strategy_id,
            version,
            allocated_capital=Decimal("500"),
            currency="USDT",
            exchange="bitget",
            mode="PAPER",
            available_balance=Decimal("10000"),
        )


async def test_capital_over_cap_rejected(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=False)
    await _link_credential(pool, user_id)

    with pytest.raises(CapitalAllocationError):
        await service.create_execution(
            user_id,
            strategy_id,
            version,
            allocated_capital=Decimal("5000"),  # 50% > unverified 10% cap
            currency="USDT",
            exchange="bitget",
            mode="PAPER",
            available_balance=Decimal("10000"),
        )


async def test_certified_strategy_allows_higher_allocation(service, pool):
    user_id = await create_test_user(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id, certified_badge=True)
    await _link_credential(pool, user_id)

    result = await service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("2000"),  # 20% — 인증 25% 한도 내
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )
    assert result.allocated_capital == Decimal("2000")
