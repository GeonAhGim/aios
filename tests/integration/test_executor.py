"""FD-8.4 통합테스트 — Executor의 LIVE 하드가드 + PAPER 제출 + FSM 전이."""
import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.exceptions import FrozenZoneLiveModeBlockedError, FrozenZonePaperAdapterBlockedError
from src.core.executor.executor import Executor
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import Order, OrderSide, OrderStatus
from src.services.condition_compiler import ConditionCompiler
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
    yield p
    await p.close()


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-executor-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=10.0)],
    )


async def _create_execution(pool: asyncpg.Pool, user_id: uuid.UUID, *, mode: str = "PAPER") -> int:
    strategy_id = f"executor-test-{uuid.uuid4().hex[:8]}"
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
                 allocated_capital, currency, status, fsm_state)
            VALUES ($1, '1.0.0', $2, 'bitget', $3, 100, 'USDT', 'RUNNING', 'BUY_ORDER_PENDING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            mode,
        )
    return row["id"]


def _approved_risk_result() -> RiskCheckResult:
    return RiskCheckResult(approved=True, rejection_reason=None, checked_rules=["daily_loss"])


def _allocation() -> AllocationDecision:
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-executor-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )


async def _fsm_state_writer_for(pool: asyncpg.Pool):
    calls: list[tuple[int, FSMState, FSMState]] = []

    async def writer(execution_id: int, expected_state: FSMState, new_state: FSMState) -> None:
        calls.append((execution_id, expected_state, new_state))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE strategy_executions SET fsm_state = $2 WHERE id = $1 AND fsm_state = $3",
                execution_id,
                new_state.value,
                expected_state.value,
            )

    return writer, calls


async def test_live_mode_is_hard_blocked_before_any_order_is_placed(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="LIVE")
    adapter = FakeExchangeAdapter()
    writer, calls = await _fsm_state_writer_for(pool)
    executor = Executor()

    with pytest.raises(FrozenZoneLiveModeBlockedError):
        await executor.execute(
            _allocation(),
            _approved_risk_result(),
            adapter,
            execution_id=execution_id,
            user_id=user_id,
            strategy_version="1.0.0",
            mode="LIVE",
            side=OrderSide.BUY,
            pending_fsm_state=FSMState.BUY_ORDER_PENDING,
            fsm_config=_fsm_config(),
            fsm_state_writer=writer,
            pool=pool,
        )

    assert adapter.place_order_call_count == 0
    assert calls == []


async def test_paper_mode_with_non_sandboxed_adapter_is_hard_blocked(pool):
    """레드팀 감사(2026-09-01-08) 회귀 — DB mode='PAPER'라도 전달된
    adapter가 스스로 sandbox 바인딩을 증명하지 못하면(예: demo_mode=False로
    잘못 구성된 real adapter) 차단해야 한다. mode 문자열만 믿지 않는다."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter(is_sandboxed=False)
    writer, calls = await _fsm_state_writer_for(pool)
    executor = Executor()

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await executor.execute(
            _allocation(),
            _approved_risk_result(),
            adapter,
            execution_id=execution_id,
            user_id=user_id,
            strategy_version="1.0.0",
            mode="PAPER",
            side=OrderSide.BUY,
            pending_fsm_state=FSMState.BUY_ORDER_PENDING,
            fsm_config=_fsm_config(),
            fsm_state_writer=writer,
            pool=pool,
        )

    assert adapter.place_order_call_count == 0
    assert calls == []


async def test_paper_mode_rejects_a_live_configured_adapter_before_order_submission(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter(is_paper_trading=False)
    writer, calls = await _fsm_state_writer_for(pool)

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await Executor().execute(
            _allocation(),
            _approved_risk_result(),
            adapter,
            execution_id=execution_id,
            user_id=user_id,
            strategy_version="1.0.0",
            mode="PAPER",
            side=OrderSide.BUY,
            pending_fsm_state=FSMState.BUY_ORDER_PENDING,
            fsm_config=_fsm_config(),
            fsm_state_writer=writer,
            pool=pool,
        )

    assert adapter.place_order_call_count == 0
    assert calls == []


async def test_risk_not_approved_raises_before_any_order_is_placed(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter()
    writer, _calls = await _fsm_state_writer_for(pool)
    executor = Executor()
    rejected = RiskCheckResult(approved=False, rejection_reason="x", checked_rules=[])

    with pytest.raises(ValueError):
        await executor.execute(
            _allocation(),
            rejected,
            adapter,
            execution_id=execution_id,
            user_id=user_id,
            strategy_version="1.0.0",
            mode="PAPER",
            side=OrderSide.BUY,
            pending_fsm_state=FSMState.BUY_ORDER_PENDING,
            fsm_config=_fsm_config(),
            fsm_state_writer=writer,
            pool=pool,
        )

    assert adapter.place_order_call_count == 0


async def test_paper_mode_synchronous_fill_advances_fsm_state(pool):
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FILLED)
    writer, calls = await _fsm_state_writer_for(pool)
    executor = Executor()

    result = await executor.execute(
        _allocation(),
        _approved_risk_result(),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=writer,
        pool=pool,
    )

    assert result.status == OrderStatus.FILLED
    assert calls == [(execution_id, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING)]

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "HOLDING"


async def test_paper_mode_pending_fill_does_not_advance_fsm_state(pool):
    """즉시 체결되지 않으면(SUBMITTED로 남으면) fsm_state를 건드리지
    않는다 — ORDER_FILLED 전이는 체결이 실제로 확인됐을 때만(FD-8.4
    처리단계 5)."""
    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.SUBMITTED)
    writer, calls = await _fsm_state_writer_for(pool)
    executor = Executor()

    result = await executor.execute(
        _allocation(),
        _approved_risk_result(),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=writer,
        pool=pool,
    )

    assert result.status == OrderStatus.SUBMITTED
    assert calls == []


async def test_submission_failure_does_not_roll_back_fsm_state(pool):
    """FD-8.4 예외상황 — 전송 실패 시 fsm_state를 되돌리지 않는다(여기서는
    fsm_state_writer가 아예 호출되지 않아야 함을 확인 — 오케스트레이터가
    이미 설정해둔 PENDING 상태 그대로 남는다)."""
    from src.core.exceptions import RetryableExchangeError

    user_id = await create_test_user(pool)
    execution_id = await _create_execution(pool, user_id, mode="PAPER")

    async def failing_place_order(order: Order) -> Order:
        raise RetryableExchangeError("네트워크 오류")

    adapter = FakeExchangeAdapter(on_place_order=failing_place_order)
    writer, calls = await _fsm_state_writer_for(pool)
    executor = Executor()

    with pytest.raises(RetryableExchangeError):
        await executor.execute(
            _allocation(),
            _approved_risk_result(),
            adapter,
            execution_id=execution_id,
            user_id=user_id,
            strategy_version="1.0.0",
            mode="PAPER",
            side=OrderSide.BUY,
            pending_fsm_state=FSMState.BUY_ORDER_PENDING,
            fsm_config=_fsm_config(),
            fsm_state_writer=writer,
            pool=pool,
        )

    assert calls == []
