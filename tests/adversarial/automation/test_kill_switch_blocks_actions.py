"""U-4 DoD 적대적 테스트: "실거래 행동(주문·헤지·kill)은 반드시 리스크·컴플라이언스
게이트를 통과해야만 실행됨 — 게이트 미통과 규칙 실행 0건" (UX-A3와 동형).

INVARIANTS.md I-09 cross-check: 최종 ALLOW는 RiskEngine 합성점과 Compliance
번들 평가 양쪽 모두를 요구한다 — 한쪽만 ALLOW인 경우도 DENY로 취급돼야 한다
(`test_execute_action.py::test_gate_decision_missing_compliance_id_denies_even_if_risk_allows`
가 그 축을 단위 테스트로 커버; 여기서는 세 행동 종류(order/hedge/kill) 전부에서
kill-switch-active 시나리오가 동일하게 막히는지를 한 번에 증명한다).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.automation.application.execute_action import execute_action
from src.foundation.automation.contracts.v1 import HedgeAction, KillAction, OrderAction
from src.foundation.risk_gate.contracts.v1 import SafetyScope
from tests.foundation.unit.automation.conftest import FakeGate, FakeNotifier, FakeSink

_ACTIONS = [
    OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
    HedgeAction(symbol="005930", hedge_symbol="KOSPI200F", quantity=Decimal("1")),
    KillAction(scope=SafetyScope.GLOBAL, reason="drawdown breach"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ACTIONS, ids=["order", "hedge", "kill"])
async def test_action_rejected_zero_executions_when_kill_switch_active(action) -> None:
    sink = FakeSink()
    result = await execute_action(
        tenant_id=uuid4(),
        rule_id=uuid4(),
        action=action,
        trace_id=uuid4(),
        gate=FakeGate(allow=False, reason="RISK_KILL_SWITCH_ACTIVE_GLOBAL"),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert sink.total_calls == 0
