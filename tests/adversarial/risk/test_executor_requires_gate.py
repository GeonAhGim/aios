"""task-1715(P0-B) 적대적 3종 — 운영 주문 경로가 게이트를 우회할 수 없음을 증명한다.

Spec: 감사 2026-09-06 P0-B("Executor 미주입 + None 통과 + require_mandate=False").
DoD: (1) 게이트 없는 Executor 주문 실패, (2) kill switch ACTIVE에서 Executor 경로
거부, (3) mandate DENY에서 거부. `Executor.execute()`가 `pre_submit_gate`를 필수
인자로 받아 `submit_order`(비펜스 폴백 경로)까지 관통시키는지 실 DB로 확인한다.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg
import pytest

from src.core.executor.executor import Executor
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.condition_compiler import ConditionCompiler
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.submit import OrderDeniedByRiskGateError
from src.services.preview_service import PreviewCondition
from tests.adversarial.risk.conftest import RecordingAdapter, seed_execution
from tests.integration.conftest import create_test_tenant

_PROVIDER = "bitget"


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-p0b-adversarial",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange=_PROVIDER,
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=10.0)],
    )


def _allocation() -> AllocationDecision:
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-p0b-adversarial",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )


async def _noop_writer(execution_id: int, expected: FSMState, new: FSMState) -> None:
    raise AssertionError("이 테스트들은 거부/실패 경로만 확인한다 — writer가 불릴 이유가 없다")


def _execute_kwargs(execution_id: int, user_id) -> dict[str, object]:
    return dict(
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode="PAPER",
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=_noop_writer,
    )


async def test_executor_without_gate_fails_closed(pool: asyncpg.Pool) -> None:
    """(1) 게이트 없는 Executor 주문 실패 — `pre_submit_gate`는 필수 kwonly
    인자다(I-01). 넘기지 않으면 주문을 시도조차 하지 않고 즉시 TypeError."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id, exchange=_PROVIDER)
    adapter = RecordingAdapter()

    with pytest.raises(TypeError):
        await Executor().execute(
            _allocation(),
            RiskCheckResult(approved=True),
            adapter,
            pool=pool,
            **_execute_kwargs(execution_id, user_id),
        )

    assert adapter.place_order_call_count == 0


async def test_executor_denies_when_kill_switch_active(pool: asyncpg.Pool) -> None:
    """(2) kill switch ACTIVE에서 Executor 경로 거부 — 실제 프로덕션 게이트
    구현체(`make_foundation_pre_submit_gate`)를 주입해도, ACCOUNT 범위
    safety_control이 켜져 있으면 거래소 호출 전에 거부돼야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id, exchange=_PROVIDER)
    adapter = RecordingAdapter()

    risk_repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        risk_repo,
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=False,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="P0-B 적대적 테스트 — Executor 경로 kill switch",
    )
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await Executor().execute(
            _allocation(),
            RiskCheckResult(approved=True),
            adapter,
            pool=pool,
            pre_submit_gate=gate,
            **_execute_kwargs(execution_id, user_id),
        )

    assert any(code.startswith("RISK_KILL_SWITCH_ACTIVE") for code in exc_info.value.reason_codes)
    assert adapter.place_order_call_count == 0


async def test_executor_denies_when_mandate_required_but_missing(pool: asyncpg.Pool) -> None:
    """(3) mandate DENY에서 거부 — `require_mandate=True`인 게이트를 주입하면
    (이 실행에는 아직 mandate_revision_id가 연결돼 있지 않으므로) RSK-002가
    RISK_MANDATE_REQUIRED로 거부한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id, exchange=_PROVIDER)
    adapter = RecordingAdapter()

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await Executor().execute(
            _allocation(),
            RiskCheckResult(approved=True),
            adapter,
            pool=pool,
            pre_submit_gate=gate,
            **_execute_kwargs(execution_id, user_id),
        )

    assert exc_info.value.reason_codes == ("RISK_MANDATE_REQUIRED",)
    assert adapter.place_order_call_count == 0
