"""20.1 통합테스트 — 실제 dev DB 대상."""
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.loader.risk_policy_loader import load_risk_policy
from src.services.execution_service import ExecutionService
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.report_service import ReportService
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
    return ExecutionService(
        pool,
        load_risk_policy(),
        pre_start_gate=make_foundation_pre_submit_gate(pool, require_mandate=False),
    )


@pytest.fixture
def report_service(pool):
    return ReportService(pool)


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


async def _create_running_execution(execution_service, pool, user_id, *, link_credential=True):
    strategy_id, version = await _create_approved_strategy(pool, user_id)
    if link_credential:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO exchange_credentials "
                "(user_id, exchange, api_key_encrypted, api_secret_encrypted) "
                "VALUES ($1, 'bitget', $2, $2)",
                user_id,
                b"dummy",
            )
    created = await execution_service.create_execution(
        user_id,
        strategy_id,
        version,
        allocated_capital=Decimal("1000"),
        currency="USDT",
        exchange="bitget",
        mode="PAPER",
        available_balance=Decimal("100000"),
    )
    await execution_service.start(created.id, user_id)
    return created.id, strategy_id, version


async def _close_position(pool, user_id, execution_id, strategy_id, *, realized_pnl, closed_at):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO positions
                (user_id, symbol, exchange, strategy_id, execution_id, quantity,
                 average_entry_price, realized_pnl, entry_time, closed_at)
            VALUES ($1, 'BTC/USDT', 'bitget', $2, $3, 0, 50000, $4, now(), $5)
            """,
            user_id,
            strategy_id,
            execution_id,
            realized_pnl,
            closed_at,
        )


async def test_empty_period_returns_empty_report_not_error(report_service, pool):
    user_id = await create_test_user(pool)

    report = await report_service.generate_report(
        user_id, date(2020, 1, 1), date(2020, 1, 31)
    )

    assert report.trade_count == 0
    assert report.total_return == Decimal("0")
    assert report.win_rate is None
    assert report.daily_pnl == []


async def test_report_matches_manually_computed_totals(execution_service, report_service, pool):
    user_id = await create_test_user(pool)
    execution_id, strategy_id, _ = await _create_running_execution(execution_service, pool, user_id)

    today = date.today()
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("100"), closed_at=today,
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("-40"), closed_at=today,
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("60"), closed_at=today,
    )

    report = await report_service.generate_report(
        user_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    assert report.trade_count == 3
    assert report.total_return == Decimal("120")  # 100 - 40 + 60
    assert report.win_rate == Decimal("200") / Decimal("3")  # 2/3 승률


async def test_positions_outside_period_excluded(execution_service, report_service, pool):
    user_id = await create_test_user(pool)
    execution_id, strategy_id, _ = await _create_running_execution(execution_service, pool, user_id)
    today = date.today()

    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("500"), closed_at=today - timedelta(days=100),
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("50"), closed_at=today,
    )

    report = await report_service.generate_report(
        user_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    assert report.trade_count == 1
    assert report.total_return == Decimal("50")


async def test_max_drawdown_computed_from_daily_cumulative_curve(
    execution_service, report_service, pool
):
    user_id = await create_test_user(pool)
    execution_id, strategy_id, _ = await _create_running_execution(execution_service, pool, user_id)
    today = date.today()

    # day0: +100 (peak=100), day1: -150 (cum=-50, drawdown=150), day2: +30 (cum=-20)
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("100"), closed_at=today - timedelta(days=2),
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("-150"), closed_at=today - timedelta(days=1),
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id,
        realized_pnl=Decimal("30"), closed_at=today,
    )

    report = await report_service.generate_report(
        user_id, today - timedelta(days=3), today + timedelta(days=1)
    )

    assert report.max_drawdown == Decimal("150")
    assert len(report.daily_pnl) == 3
    assert report.daily_pnl[-1].cumulative_pnl == Decimal("-20")


async def test_execution_id_filter_scopes_to_single_execution(
    execution_service, report_service, pool
):
    user_id = await create_test_user(pool)
    execution_a, strategy_a, _ = await _create_running_execution(execution_service, pool, user_id)
    execution_b, strategy_b, _ = await _create_running_execution(
        execution_service, pool, user_id, link_credential=False
    )
    today = date.today()

    await _close_position(
        pool, user_id, execution_a, strategy_a, realized_pnl=Decimal("100"), closed_at=today
    )
    await _close_position(
        pool, user_id, execution_b, strategy_b, realized_pnl=Decimal("999"), closed_at=today
    )

    report = await report_service.generate_report(
        user_id, today - timedelta(days=1), today + timedelta(days=1), execution_id=execution_a
    )

    assert report.trade_count == 1
    assert report.total_return == Decimal("100")


async def test_strategy_contributions_grouped_correctly(execution_service, report_service, pool):
    user_id = await create_test_user(pool)
    execution_id, strategy_id, version = await _create_running_execution(
        execution_service, pool, user_id
    )
    today = date.today()

    await _close_position(
        pool, user_id, execution_id, strategy_id, realized_pnl=Decimal("70"), closed_at=today
    )
    await _close_position(
        pool, user_id, execution_id, strategy_id, realized_pnl=Decimal("30"), closed_at=today
    )

    report = await report_service.generate_report(
        user_id, today - timedelta(days=1), today + timedelta(days=1)
    )

    assert len(report.strategy_contributions) == 1
    contribution = report.strategy_contributions[0]
    assert contribution.strategy_id == strategy_id
    assert contribution.strategy_version == version
    assert contribution.realized_pnl == Decimal("100")
    assert contribution.trade_count == 2
