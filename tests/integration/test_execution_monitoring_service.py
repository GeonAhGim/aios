"""16.4 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_monitoring_service import ExecutionMonitoringService
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from tests.integration.conftest import create_test_tenant


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
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
    )


@pytest.fixture
def monitoring_service(pool):
    return ExecutionMonitoringService(pool)


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


async def _create_running_execution(execution_service, pool, user_id, *, link_credential=True):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
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


async def test_execution_with_no_positions_reports_zero_pnl(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    execution_id, _ = await _create_running_execution(execution_service, pool, user_id)

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")
    assert card.days_since_start == 0


async def test_two_running_executions_track_pnl_independently(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    exec_a, strategy_a = await _create_running_execution(execution_service, pool, user_id)
    exec_b, strategy_b = await _create_running_execution(
        execution_service, pool, user_id, link_credential=False
    )

    await _insert_position(
        pool, user_id, exec_a, strategy_a, realized=Decimal("100"), unrealized=Decimal("50")
    )
    await _insert_position(
        pool, user_id, exec_b, strategy_b, realized=Decimal("-30"), unrealized=Decimal("10")
    )

    cards = await monitoring_service.list_for_user(user_id)

    card_a = next(c for c in cards if c.execution_id == exec_a)
    card_b = next(c for c in cards if c.execution_id == exec_b)
    assert card_a.realized_pnl == Decimal("100")
    assert card_a.unrealized_pnl == Decimal("50")
    assert card_b.realized_pnl == Decimal("-30")
    assert card_b.unrealized_pnl == Decimal("10")


async def test_multiple_positions_in_same_execution_sum_correctly(
    execution_service, monitoring_service, pool
):
    user_id = await create_test_tenant(pool)
    execution_id, strategy_id = await _create_running_execution(execution_service, pool, user_id)

    await _insert_position(
        pool, user_id, execution_id, strategy_id, realized=Decimal("10"), unrealized=Decimal("5")
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, unrealized_pnl, realized_pnl, entry_time)
            VALUES ($1, 'ETH/USDT', 'bitget', $2, $3, 1.0, 3000, 15, 20, now())
            """,
            user_id,
            strategy_id,
            execution_id,
        )

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == execution_id)
    assert card.realized_pnl == Decimal("30")
    assert card.unrealized_pnl == Decimal("20")


async def test_no_executions_returns_empty_list_not_error(monitoring_service, pool):
    user_id = await create_test_tenant(pool)

    cards = await monitoring_service.list_for_user(user_id)

    assert cards == []


async def test_unknown_user_id_returns_empty_list_not_error(monitoring_service):
    cards = await monitoring_service.list_for_user(uuid4())

    assert cards == []


async def test_created_but_not_started_execution_has_none_days_since_start(
    execution_service, monitoring_service, pool
):
    """created_execution() 만 호출하고 start()는 호출하지 않으면 started_at이
    NULL이다 — days_since_start는 None이어야 하고(경계값), 실행 자체는
    목록에서 사라지지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    await _link_credential(pool, user_id)
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("500"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("10000"),
    )

    cards = await monitoring_service.list_for_user(user_id)

    card = next(c for c in cards if c.execution_id == created.id)
    assert card.days_since_start is None
    assert card.realized_pnl == Decimal("0")
    assert card.unrealized_pnl == Decimal("0")


async def test_executions_are_isolated_per_user(execution_service, monitoring_service, pool):
    """다른 사용자의 실행이 조회 결과에 섞여 들어오지 않는다(negative — 테넌트
    격리 위반이 없어야 함, INVARIANTS 준수 확인)."""
    user_a = await create_test_tenant(pool)
    user_b = await create_test_tenant(pool)
    exec_a, _ = await _create_running_execution(execution_service, pool, user_a)
    exec_b, _ = await _create_running_execution(execution_service, pool, user_b)

    cards_a = await monitoring_service.list_for_user(user_a)
    cards_b = await monitoring_service.list_for_user(user_b)

    assert {c.execution_id for c in cards_a} == {exec_a}
    assert {c.execution_id for c in cards_b} == {exec_b}


async def test_pool_acquire_failure_propagates_fail_closed(monitoring_service, monkeypatch):
    """의존성(DB pool) 장애 시 조용히 빈 목록을 반환하지 않고 예외를 그대로
    전파해야 한다(fail-closed 기본 원칙, CLAUDE.md §3) — 실패주입 테스트."""

    class _FailingPool:
        def acquire(self):
            raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(monitoring_service, "_pool", _FailingPool())

    with pytest.raises(RuntimeError, match="connection pool exhausted"):
        await monitoring_service.list_for_user(uuid4())
