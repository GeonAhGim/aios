"""FD-8.1~8.4 실행 루프 통합테스트 — 책임 분할(ADR-2026-09-10-C LOC 규율,
task-10199/task-12088): 이 파일은 StrategyEngine/PortfolioEngine/RiskEngine/
Executor 각 단계에서 예외·비정상 반환 타입이 전파되는 경로를 담당한다.
정상 진입/신호없음/자본배분 거부/equity 영속 경로는 test_execution_tick.py,
데이터불신 차단 경로는 test_execution_tick_distrust.py, 일시정지/동시성
경합은 test_execution_tick_pause_and_race.py, 리스크 결정 기록(WORM)은
test_execution_tick_risk_decisions.py — 공용 픽스처/헬퍼는
_execution_tick_helpers.py.
"""

from decimal import Decimal

import pytest

from src.data.models.trading import AccountBalance, OrderStatus
from src.services.execution_loop.tick import run_execution_tick
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


async def test_risk_engine_exception_propagates_and_leaves_fsm_idle(pool, monkeypatch):
    """실패주입 — RiskEngine.check_decision()가 예외를 던지면 tick 전체가
    실패하고 fsm_state는 IDLE 그대로 남아야 한다(I-07: 안전 게이트는
    판단 불가를 승인으로 취급하지 않는다, fail-closed)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    engines = _engines()

    def fake_check_decision(*args, **kwargs):
        raise RuntimeError("의도적 리스크 엔진 실패")

    monkeypatch.setattr(engines["risk_engine"], "check_decision", fake_check_decision)

    with pytest.raises(RuntimeError, match="의도적 리스크 엔진 실패"):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_strategy_engine_evaluation_error_propagates(pool, monkeypatch):
    """실패주입 — StrategyEngine.evaluate()가 예외를 던지면 tick 전체가
    실패해야 한다(FSM 상태 변경 없이 예외가 외부로 전파)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_evaluate(*args, **kwargs):
        raise RuntimeError("strategy evaluation failed")

    monkeypatch.setattr(engines["strategy_engine"], "evaluate", fake_evaluate)

    with pytest.raises(RuntimeError, match="strategy evaluation failed"):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로 남아있어야 함(중간 상태 갱신 없음).
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_strategy_engine_invalid_signal_type_raises(pool, monkeypatch):
    """negative test — StrategyEngine.evaluate()가 None(신호 없음)이 아닌
    비정상 타입(예: int)을 반환하면 tick 전체가 ValueError를 던져야 한다.
    평가 산출물은 dict(signal: str, ...) 구조여야 한다(I-07: 검증 게이트
    hard-fail)."""
    from src.core.strategy.engine import StrategyEngine

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_evaluate(*args, **kwargs):
        return 42  # 비정상 타입 — dict가 아님

    monkeypatch.setattr(StrategyEngine, "evaluate", fake_evaluate)

    with pytest.raises(AttributeError):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로.
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_strategy_engine_empty_return_raises(pool, monkeypatch):
    """negative test — StrategyEngine.evaluate()가 빈 dict({})를 반환하면
    tick 전체가 AttributeError를 던져야 한다. 빈 dict는 signal 키가 없어
    PortfolioEngine이 AttributeError를 일으킨다(I-07: 검증 게이트 hard-fail)."""
    from src.core.strategy.engine import StrategyEngine

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_evaluate(*args, **kwargs):
        return {}  # 빈 딕셔너리 — signal 키 없음

    monkeypatch.setattr(StrategyEngine, "evaluate", fake_evaluate)

    with pytest.raises(AttributeError):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로.
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_portfolio_engine_allocation_error_propagates(pool, monkeypatch):
    """실패주입 — PortfolioEngine.allocate()가 예외를 던지면 tick 전체가
    실패해야 한다. 신호는 평가됐으나 배분 단계에서 오류."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_allocate(*args, **kwargs):
        raise RuntimeError("portfolio allocation failed")

    monkeypatch.setattr(engines["portfolio_engine"], "allocate", fake_allocate)

    with pytest.raises(RuntimeError, match="portfolio allocation failed"):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로 남아있어야 함.
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_portfolio_engine_invalid_allocation_result_type(pool, monkeypatch):
    """negative test — PortfolioEngine.allocate()가 dict가 아닌 비정상 타입(
    예: int)을 반환하면 tick 전체가 AttributeError를 던져야 한다. 배분 산출물은
    dict(quantity: Decimal, ...) 구조여야 한다(I-07: 검증 게이트 hard-fail)."""
    from src.core.portfolio.engine import PortfolioEngine

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_allocate(*args, **kwargs):
        return 42  # 비정상 타입 — dict가 아님

    monkeypatch.setattr(PortfolioEngine, "allocate", fake_allocate)

    with pytest.raises(AttributeError):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로.
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_portfolio_engine_empty_allocation_result(pool, monkeypatch):
    """negative test — PortfolioEngine.allocate()가 빈 dict({})를 반환하면
    tick 전체가 AttributeError를 던져야 한다. 빈 dict는 속성이 없어
    build_order_intent가 allocation.symbol 접근 시 AttributeError를 일으킨다
    (I-07: 검증 게이트 hard-fail)."""
    from src.core.portfolio.engine import PortfolioEngine

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    def fake_allocate(*args, **kwargs):
        return {}  # 빈 dict — symbol/quantity 속성 없음

    monkeypatch.setattr(PortfolioEngine, "allocate", fake_allocate)

    with pytest.raises(AttributeError):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로.
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_executor_failure_propagates_and_leaves_fsm_pending(pool, monkeypatch):
    """실패주입 — Executor.execute()가 예외를 던지면 tick 전체가 실패해야
    한다. 신호→배분→리스크는 통과했으나 실행 단계에서 오류. FSM은
    BUY_ORDER_PENDING으로 남는다(주문 제출 전이 이미 적용됨)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    engines = _engines()

    def fake_execute(*args, **kwargs):
        raise RuntimeError("executor failed to place order")

    monkeypatch.setattr(engines["executor"], "execute", fake_execute)

    with pytest.raises(RuntimeError, match="executor failed to place order"):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM은 BUY_ORDER_PENDING으로 남아있어야 함(주문 제출 전이 적용 후
    # executor.execute()에서 예외 발생).
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"
