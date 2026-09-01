"""FD-8.1~8.4 실행 루프 통합테스트 — StrategyEngine→PortfolioEngine→
RiskEngine→Executor 전체 파이프라인 왕복(거래소는 FakeExchangeAdapter 대역).
"""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.executor.executor import Executor
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.engine import PortfolioEngine
from src.core.risk.engine import RiskEngine
from src.core.strategy.engine import StrategyEngine
from src.data.models.trading import AccountBalance, OrderStatus
from src.services.condition_compiler import ConditionCompiler
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.tick import run_execution_tick
from src.services.preview_service import PreviewCondition
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글톤 행이라 다른 테스트 파일(예:
        # test_circuit_breaker.py)이 남긴 상태에 오염될 수 있다 — 그 파일의
        # 격리 관례를 그대로 따른다.
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _fsm_definition(*, entry_threshold: float) -> dict:
    compiled = ConditionCompiler().compile(
        strategy_id="tick-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=entry_threshold
            )
        ],
        exit_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator=">", threshold=999999.0
            )
        ],
        stop_loss_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=0.0
            )
        ],
    )
    return json.loads(compiled.model_dump_json())


async def _create_execution(
    pool: asyncpg.Pool,
    user_id: uuid.UUID,
    *,
    entry_threshold: float = 100.0,
    allocated_capital: Decimal = Decimal("1000"),
) -> int:
    strategy_id = f"tick-test-{uuid.uuid4().hex[:8]}"
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
            json.dumps(_fsm_definition(entry_threshold=entry_threshold)),
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


def _engines():
    return {
        "strategy_engine": StrategyEngine(),
        "portfolio_engine": PortfolioEngine(),
        "risk_engine": RiskEngine(load_risk_policy()),
        "executor": Executor(),
        "equity_tracker": ExecutionEquityTracker(),
        "policy": load_risk_policy(),
    }


async def test_entry_signal_submits_and_fills_order_advances_to_holding(pool):
    user_id = await create_test_user(pool)
    # SMA(close=50, 5기간) = 50 < 100 → 진입 조건 항상 충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order = await conn.fetchrow(
            "SELECT status, side, quantity FROM orders WHERE execution_id = $1", execution_id
        )
    assert execution["fsm_state"] == "HOLDING"
    assert order["status"] == "FILLED"
    assert order["side"] == "BUY"
    # allocated_capital(1000) / current_price(50) = 20
    assert order["quantity"] == Decimal("20.0000000000")


async def test_no_signal_tick_does_nothing(pool):
    user_id = await create_test_user(pool)
    # SMA(50) < -1 은 항상 거짓 — 진입 조건 미충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 30)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_risk_rejection_leaves_fsm_state_idle_for_retry(pool):
    """자본배분 상한 초과(미인증 전략 10%인데 50% 요청) — RiskEngine이
    거부하면 fsm_state는 IDLE 그대로 남아 다음 틱에 재평가돼야 한다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 30,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_paused_execution_is_skipped(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE id = $1",
            execution_id,
        )
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 30)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
