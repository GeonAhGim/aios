from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.automation.application.execute_action import execute_action
from src.foundation.automation.contracts.v1 import KillAction, NotifyAction, OrderAction
from src.foundation.risk_gate.contracts.v1 import SafetyScope

from .conftest import FakeGate, FakeNotifier, FakeSink


@pytest.mark.asyncio
async def test_notify_action_success(tenant_id) -> None:
    notifier = FakeNotifier(ok=True)
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=NotifyAction(message_template="hi"),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=FakeSink(),
        notifier=notifier,
    )
    assert result.executed is True
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_notify_action_failure_is_reported_not_swallowed(tenant_id) -> None:
    """장애 주입: notifier가 실패를 반환하면 성공으로 위장하지 않는다."""
    notifier = FakeNotifier(ok=False, error="TELEGRAM_502")
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=NotifyAction(message_template="hi"),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=FakeSink(),
        notifier=notifier,
    )
    assert result.executed is False
    assert result.error == "TELEGRAM_502"


@pytest.mark.asyncio
async def test_order_action_executes_when_gate_allows(tenant_id) -> None:
    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is True
    assert len(sink.order_calls) == 1


@pytest.mark.asyncio
async def test_order_action_rejected_when_kill_switch_active(tenant_id) -> None:
    """red-gate repro: 게이트가 kill-switch 활성으로 DENY하면 sink 호출 0건."""
    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=FakeGate(allow=False, reason="RISK_KILL_SWITCH_ACTIVE_GLOBAL"),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.error == "RISK_KILL_SWITCH_ACTIVE_GLOBAL"
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_kill_action_also_requires_gate_allow(tenant_id) -> None:
    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=KillAction(
            scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref="dep-1", reason="drawdown breach"
        ),
        trace_id=uuid4(),
        gate=FakeGate(allow=False),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_gate_decision_missing_compliance_id_denies_even_if_risk_allows(tenant_id) -> None:
    """I-09 적대적 테스트: RiskEngine만 ALLOW하고 Compliance 증거(decision id)가
    없으면 두 독립 권위를 모두 통과한 게 아니므로 전체 DENY다(fail-closed)."""
    from src.foundation.automation.ports.gate import ActionIntent, GateDecision
    from src.foundation.risk_gate.contracts.v1 import RiskOutcome

    class OneSidedGate:
        async def check(self, intent: ActionIntent) -> GateDecision:
            return GateDecision(
                risk_outcome=RiskOutcome.ALLOW,
                risk_decision_id=uuid4(),
                compliance_outcome=RiskOutcome.ALLOW,
                compliance_decision_id=None,  # 증거 없음 -> I-09 위반
            )

    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=OneSidedGate(),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert sink.total_calls == 0
