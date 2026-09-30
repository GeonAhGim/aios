"""FD-8.1~8.4 실행 루프 통합테스트 — StrategyEngine→PortfolioEngine→
RiskEngine→Executor 전체 파이프라인 왕복(거래소는 FakeExchangeAdapter 대역).

책임 분할(task-10199, ADR-2026-09-10-C LOC 규율): 데이터불신(distrust) 차단
경로는 test_execution_tick_distrust.py, 일시정지/동시성 경합은
test_execution_tick_pause_and_race.py, 리스크 결정 기록(WORM)은
test_execution_tick_risk_decisions.py — 공용 픽스처/헬퍼는
_execution_tick_helpers.py. 이 파일은 정상 진입/신호없음/자본배분 거부/
에러 전파/equity 기준점 영속 경로를 담당한다.
"""

from decimal import Decimal

import asyncpg
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


async def test_entry_signal_submits_and_fills_order_advances_to_holding(pool):
    user_id = await create_test_tenant(pool)
    # SMA(close=50, 5기간) = 50 < 100 → 진입 조건 항상 충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
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
    user_id = await create_test_tenant(pool)
    # SMA(50) < -1 은 항상 거짓 — 진입 조건 미충족.
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)

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

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_equity_baseline_persists_and_survives_simulated_restart(pool):
    """PM 배정 ③(agent-platform-12, 2026-09-02) 회귀 테스트 — 이전엔
    일손실/MDD 기준점이 프로세스 메모리에만 있어 재시작하면 유실됐다.
    "재시작"은 매번 새 ExecutionEquityTracker(빈 메모리)로 tick을 다시
    부르는 것으로 시뮬레이션한다(_engines()가 매번 새 인스턴스를 만듦).
    RiskEngine이 항상 거부하도록(자본배분 상한 초과, test_risk_rejection_
    leaves_fsm_state_idle_for_retry와 동일 설정) fsm_state를 IDLE에
    묶어둬 매 tick마다 assemble_account_state가 반복 호출되게 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    first_tick_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    # "프로세스 1" — 최초 tick, 빈 메모리 tracker로 기준점을 처음 만든다.
    await run_execution_tick(pool, first_tick_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_date"] is not None
    assert row["equity_day_start_value"] == Decimal("10000.0000000000")
    assert row["equity_peak_value"] == Decimal("10000.0000000000")

    # "프로세스 2"(재시작 시뮬레이션) — 새 tracker(빈 메모리)로 다시 tick,
    # 이번엔 잔고가 바뀐 상태(9000)다. DB에서 기준점을 seed()로 복구해야
    # "오늘 시작"이 지금 이 순간(9000)으로 리셋되지 않고 원래 10000을
    # 그대로 이어받는다 — 그래야 오늘 이미 나던 손실이 재시작으로
    # 사라지지 않는다.
    second_tick_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("9000"), available=Decimal("9000")
        ),
    )
    await run_execution_tick(pool, second_tick_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        row_after_restart = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    # 시작 기준점은 그대로 10000 유지(재시작으로 리셋되지 않음).
    assert row_after_restart["equity_day_start_date"] == row["equity_day_start_date"]
    assert row_after_restart["equity_day_start_value"] == Decimal("10000.0000000000")
    # peak은 10000 그대로(9000 < 10000이라 갱신 안 됨) — 이것도 기준점이
    # 실제로 이어받아졌다는 방증.
    assert row_after_restart["equity_peak_value"] == Decimal("10000.0000000000")


async def test_nonexistent_execution_id_raises_value_error(pool):
    """LB-12 — 존재하지 않는 execution_id를 전달하면 ValueError를 던진다.
    _load_execution_context가 "존재하지 않는 실행입니다" 메시지로 단언한다."""
    with pytest.raises(ValueError, match="존재하지 않는 실행"):
        await run_execution_tick(pool, FakeExchangeAdapter(), 99999999, **_engines())


async def test_invalid_fsm_state_raises_value_error(pool):
    """LB-12 — DB CHECK 제약 조건이 FSMState enum 범위를 벗어난 값을
    거부한다. strategy_executions_fsm_state_check 제약이
    ENUM_TO_CHAR(FSMState) 범위만 허용하므로 'BOGUS_STATE'은 INSERT/UPDATE
    시 CheckViolationError를 던진다. (DB constraint가 코드보다 먼저
    무효 상태를 차단함을 검증.)"""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE strategy_executions SET fsm_state = 'BOGUS_STATE' WHERE id = $1",
                execution_id,
            )


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
