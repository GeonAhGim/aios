"""task-1717 P0-D 통합테스트 — 운영 경로(Executor)에서 orders.risk_decision_id가
채워지는지 실 DB로 증명한다.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.6, §5 "orders INSERT with
risk_decision_id" 행. 감사(전수감사 2026-09-06 P0-D): `foundation_gate.py`가
내리는 실제 결정이 `risk_decision` WORM에 기록되지 않아 `decision_id`가
항상 None이었고, `Executor.execute()`는 `submit_order`만 호출해
`fenced_submit.submit_with_fence`(따라서 orders.risk_decision_id)가 운영
경로에서 0회 호출됐다. 이 테스트는 `make_foundation_pre_submit_gate()` →
`evaluate_submission_gate()` → `Executor.execute()`(submit_with_fence 경유)
전체를 실제 조립부와 같은 방식으로 엮어 orders 행의 risk_decision_id가
그 결정의 decision_id와 일치함을 확인한다.
"""

from __future__ import annotations

import os
from decimal import Decimal

import asyncpg
import pytest

from src.core.executor.executor import Executor
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide, OrderStatus
from src.services.condition_compiler import ConditionCompiler
from src.services.execution_loop.pre_submit_check import evaluate_submission_gate
from src.services.order_service.fenced_submit_wiring import (
    make_decision_reader,
    make_fence_reader_factory,
)
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateOutcome
from src.services.preview_service import PreviewCondition
from tests.adversarial.risk.conftest import RecordingAdapter, seed_execution
from tests.integration.conftest import create_test_tenant

_PROVIDER = "bitget"


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-op-wiring-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange=_PROVIDER,
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=10.0)],
    )


async def _noop_writer(execution_id: int, expected: FSMState, new: FSMState) -> None:
    raise AssertionError("SUBMITTED(비-FILLED) 응답에서는 fsm_state_writer가 불릴 이유가 없다")


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    yield p
    await p.close()


async def test_executor_via_submit_with_fence_fills_orders_risk_decision_id(pool: asyncpg.Pool):
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id, exchange=_PROVIDER)

    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    allocation = AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-op-wiring-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )

    gate_decision = await evaluate_submission_gate(
        gate,
        user_id=user_id,
        execution_id=execution_id,
        exchange=_PROVIDER,
        symbol=allocation.symbol,
        side=OrderSide.BUY.value,
        quantity=allocation.approved_quantity,
    )
    assert gate_decision is not None
    assert gate_decision.outcome == GateOutcome.ALLOW
    assert gate_decision.decision_id is not None

    read_fences = make_fence_reader_factory(pool)(user_id, _PROVIDER, f"exec:{execution_id}")
    decision_reader = make_decision_reader(pool)
    adapter = RecordingAdapter()  # place_order_result_status 기본값 SUBMITTED

    submitted = await Executor().execute(
        allocation,
        RiskCheckResult(approved=True),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=_noop_writer,
        pool=pool,
        pre_submit_gate=gate,
        gate_decision=gate_decision,
        read_fences=read_fences,
        decision_reader=decision_reader,
    )

    assert submitted.status == OrderStatus.SUBMITTED
    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT risk_decision_id FROM orders WHERE order_id = $1", submitted.order_id
        )
    assert row is not None
    assert row["risk_decision_id"] == gate_decision.decision_id


async def test_executor_falls_back_to_plain_submit_without_fence_wiring(pool: asyncpg.Pool):
    """gate_decision은 있지만 read_fences/decision_reader가 없으면(기존
    호출부와의 호환) submit_order로 폴백한다 — risk_decision_id는 안 채워
    지지만 제출 자체는 회귀 없이 성공해야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id, exchange=_PROVIDER)
    allocation = AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-op-wiring-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )
    adapter = RecordingAdapter()
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    submitted = await Executor().execute(
        allocation,
        RiskCheckResult(approved=True),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=_noop_writer,
        pool=pool,
        pre_submit_gate=gate,
    )

    assert submitted.status == OrderStatus.SUBMITTED
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT risk_decision_id FROM orders WHERE order_id = $1", submitted.order_id
        )
    assert row is not None and row["risk_decision_id"] is None
