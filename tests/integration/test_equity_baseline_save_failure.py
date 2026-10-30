"""FD-8.3 equity_baseline — save_failure_raises (split from test_execution_tick_equity_deepen).

실패주입/부하 테스트가 공용 pool 픽스처의 event loop와 간섭하지 않도록
분리한다. 원본: test_execution_tick_equity_deepen.py::test_equity_baseline_save_failure_raises.
"""

from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import AccountBalance
from src.services.execution_loop import equity_tracker
from src.services.execution_loop.tick import run_execution_tick
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


async def test_equity_baseline_save_failure_raises(pool, monkeypatch):
    """실패주입 — save_equity_baseline()가 예외를 던지면 tick 전체가
    실패해야 한다(FSM 상태 변경 없이 예외가 외부로 전파).
    equity_tracker.record_and_persist_equity가 DB 저장 실패 시
    계산된 daily_pnl/drawdown도 유실된다."""
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
