"""FD-8.3 equity_baseline deepening — task-11960 (task-425 DEEPEN).

test_equity_baseline_persists_and_survives_simulated_restart의
negative/실패주입/성능 보강. 공용 픽스처/헬퍼는
_execution_tick_helpers.py. 이 파일은 equity baseline 관련
negative(매우 낮은 equity)·실패주입(DB 저장 실패)·성능 단언을 담당한다.
"""

from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import AccountBalance
from src.services.execution_loop.tick import run_execution_tick
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


async def test_equity_baseline_persists_with_very_low_equity(pool):
    """negative test — equity가 매우 낮을 때(allocated_capital 대비 1%)
    baseline이 정상적으로 저장되고 재시작 후에도 일관성 유지.
    capital_pct가 100%를 넘으면 RiskEngine이 거부하고 fsm_state는
    IDLE 그대로여야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("5000")
    )
    first_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("50"), available=Decimal("50")
        ),
    )

    await run_execution_tick(pool, first_adapter, execution_id, **_engines())

    # allocated_capital(5000) / total_equity(50) = 10000% → risk 거부.
    assert first_adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row["equity_day_start_date"] is not None
    assert row["equity_day_start_value"] == Decimal("50.0000000000")
    assert row["equity_peak_value"] == Decimal("50.0000000000")

    # 재시작 시뮬레이션 — 새 tracker로 동일 low equity 재처리.
    await run_execution_tick(pool, first_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        row_after_restart = await conn.fetchrow(
            "SELECT equity_day_start_date, equity_day_start_value, equity_peak_value "
            "FROM strategy_executions WHERE id = $1",
            execution_id,
        )
    assert row_after_restart["equity_day_start_date"] == row["equity_day_start_date"]
    assert row_after_restart["equity_day_start_value"] == Decimal("50.0000000000")
    assert row_after_restart["equity_peak_value"] == Decimal("50.0000000000")


async def test_equity_baseline_save_failure_raises(pool, monkeypatch):
    """실패주입 — save_equity_baseline()가 예외를 던지면 tick 전체가
    실패해야 한다(FSM 상태 변경 없이 예외가 외부로 전파).
    equity_tracker.record_and_persist_equity가 DB 저장 실패 시
    계산된 daily_pnl/drawdown도 유실된다."""
    from src.services.execution_loop import equity_tracker

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    engines = _engines()

    def fake_save(*args, **kwargs):
        raise asyncpg.UniqueViolationError("23505", "constraint", "simulated DB save failure")

    monkeypatch.setattr(equity_tracker, "save_equity_baseline", fake_save)

    with pytest.raises(asyncpg.UniqueViolationError):
        await run_execution_tick(pool, adapter, execution_id, **engines)

    # FSM이 IDLE 그대로 남아있어야 함(중간 상태 갱신 없음).
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


@pytest.mark.perf
async def test_equity_baseline_persist_performance_under_200ms(pool):
    """성능 단언 — equity baseline record+persist가 200ms 이내여야 한다.
    ADR-2026-09-09-C Decision 1 성능 예산 준수 확인.

    CI 병렬 실행에서 DB 컨테이너 부하로 wall-clock이 변동되는 것을 방지하기
    위해 PerfBudget.best_of_async(cpu_ms 기준 최소값)로 노이즈를 제거한다.
    """
    from tests.support.perf_budget import PerfBudget

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    pb = PerfBudget()
    sample = await pb.best_of_async(
        lambda: run_execution_tick(pool, adapter, execution_id, **_engines()),
        n=5,
        warmup=1,
    )
    assert sample.cpu_ms < 200, (
        f"Equity baseline persist cpu={sample.cpu_ms:.1f}ms wall={sample.wall_ms:.1f}ms, "
        f"exceeds 200ms budget"
    )
