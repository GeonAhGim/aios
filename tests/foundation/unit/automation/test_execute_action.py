from __future__ import annotations

import time
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.trading import OrderSide
from src.foundation.automation.application.execute_action import execute_action
from src.foundation.automation.contracts.v1 import (
    HedgeAction,
    KillAction,
    NotifyAction,
    OrderAction,
)
from src.foundation.automation.flags import FEATURE_FLAG_NAME
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


def test_order_action_rejects_non_positive_quantity() -> None:
    """negative: quantity<=0은 도메인 불변식 위반 — 게이트/싱크에 도달하기 전에 거부된다."""
    with pytest.raises(ValidationError):
        OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("0"))


def test_hedge_action_rejects_negative_quantity() -> None:
    """negative: HedgeAction도 동일한 quantity>0 불변식을 공유한다."""
    with pytest.raises(ValidationError):
        HedgeAction(symbol="005930", hedge_symbol="251340", quantity=Decimal("-1"))


@pytest.mark.asyncio
async def test_feature_flag_off_blocks_order_action_before_gate(
    tenant_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: U-4a 피처 플래그가 꺼지면 gate/sink는 한 번도 호출되지 않는다."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "0")
    gate = FakeGate(allow=True)
    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=gate,
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.detail == "feature_disabled"
    assert len(gate.calls) == 0
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_feature_flag_off_blocks_notify_action_too(
    tenant_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: 플래그가 꺼지면 게이트를 우회하는 NotifyAction도 차단된다."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "0")
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
    assert result.executed is False
    assert result.detail == "feature_disabled"
    assert len(notifier.calls) == 0


@pytest.mark.asyncio
async def test_notifier_raising_exception_is_not_swallowed(tenant_id) -> None:
    """장애 주입: notifier가 예외를 던지면 execute_action은 이를 삼키지 않고
    전파한다 -- 실패를 executed=True로 위장하면 안 된다(fail-closed)."""

    class RaisingNotifier:
        async def send(self, notification):
            raise ConnectionError("TELEGRAM_UNREACHABLE")

    with pytest.raises(ConnectionError, match="TELEGRAM_UNREACHABLE"):
        await execute_action(
            tenant_id=tenant_id,
            rule_id=uuid4(),
            action=NotifyAction(message_template="hi"),
            trace_id=uuid4(),
            gate=FakeGate(allow=True),
            sink=FakeSink(),
            notifier=RaisingNotifier(),
        )


@pytest.mark.asyncio
async def test_order_action_latency_budget(tenant_id) -> None:
    """성능 단언: fake 의존성만 쓰는 execute_action 1회 호출은 50ms 예산 내에서
    끝난다(순수 조율 로직에 I/O 지연이 섞이지 않았는지 회귀 감지)."""
    sink = FakeSink()
    start = time.perf_counter()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=sink,
        notifier=FakeNotifier(),
    )
    elapsed = time.perf_counter() - start
    assert result.executed is True
    assert elapsed < 0.05


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
