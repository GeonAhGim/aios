"""FD-8/R-32 §3.9 — RiskEngine DENY/REDUCE 결정의 WORM 기록·수량 축소
경로 전용(test_execution_tick.py에서 task-10199로 분리).
"""

from decimal import Decimal

import asyncpg
import pytest

from src.core.event_bus.in_process import InProcessEventBus
from src.core.risk.decision import RiskOutcome
from src.data.models.trading import AccountBalance, OrderStatus
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.services.execution_loop.tick import run_execution_tick
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


class _SpyRecorder(RiskDecisionRecorder):
    """실제 WORM insert는 그대로 수행하고 호출 횟수만 센다(R-32 t4 검증)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        super().__init__(pool, PostgresDecisionRepository(pool), InProcessEventBus())
        self.calls = 0

    async def record(self, decision, inputs, *, actor: str) -> None:  # type: ignore[override]  # 상위보다 좁은 시그니처(호출 횟수 계측 전용 spy, R-32 t4)
        self.calls += 1
        await super().record(decision, inputs, actor=actor)


async def test_deny_decision_is_recorded_before_return_and_fsm_untouched(pool):
    """R-32 §3.9 t4~t5 — 거부(DENY)도 recorder.record()로 WORM에 기록된 뒤,
    FSM은 건드리지 않고(IDLE 유지) executor도 부르지 않는다. 자본배분 상한
    초과(미인증 10% vs 요청 50%)로 DENY를 유도한다."""
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
    recorder = _SpyRecorder(pool)

    await run_execution_tick(pool, adapter, execution_id, **_engines(), recorder=recorder)

    assert recorder.calls == 1
    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        rows = await conn.fetch(
            "SELECT outcome, reason_codes FROM risk_decision "
            "WHERE gate_kind = 'PRE_TRADE' AND execution_ref = $1",
            f"exec:{execution_id}",
        )
    assert fsm_state == "IDLE"
    assert [row["outcome"] for row in rows] == ["DENY"]
    assert any(code.startswith("RISK_") for code in rows[0]["reason_codes"])


async def test_reduce_decision_shrinks_approved_quantity_passed_to_executor(pool, monkeypatch):
    """R-32 §3.9 t6 — REDUCE 결정이면 approved_quantity를 obligation
    `REDUCE_QUANTITY_TO:<qty>` 값으로 축소해 executor에 전달한다. 실제
    엔진 결정(ALLOW, 수량 20)을 REDUCE·수량 5로 바꿔 끼운다."""
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
    real_check_decision = engines["risk_engine"].check_decision

    def reduce_to_five(inputs):
        decision = real_check_decision(inputs)
        assert decision.outcome == RiskOutcome.ALLOW  # 전제: 원 결정은 ALLOW(수량 20)
        return decision.model_copy(
            update={"outcome": RiskOutcome.REDUCE, "obligations": ("REDUCE_QUANTITY_TO:5",)}
        )

    monkeypatch.setattr(engines["risk_engine"], "check_decision", reduce_to_five)
    recorder = _SpyRecorder(pool)

    await run_execution_tick(pool, adapter, execution_id, **engines, recorder=recorder)

    assert recorder.calls == 1
    assert adapter.place_order_call_count == 1
    assert adapter.placed_orders[-1].quantity == Decimal("5")
    async with pool.acquire() as conn:
        outcome = await conn.fetchval(
            "SELECT outcome FROM risk_decision WHERE gate_kind = 'PRE_TRADE' "
            "AND execution_ref = $1",
            f"exec:{execution_id}",
        )
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert outcome == "REDUCE"
    assert fsm_state == "HOLDING"
