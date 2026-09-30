"""U-4 DoD 적대적 테스트: "실거래 행동(주문·헤지·kill)은 반드시 리스크·컴플라이언스
게이트를 통과해야만 실행됨 — 게이트 미통과 규칙 실행 0건" (UX-A3와 동형).

INVARIANTS.md I-09 cross-check: 최종 ALLOW는 RiskEngine 합성점과 Compliance
번들 평가 양쪽 모두를 요구한다 — 한쪽만 ALLOW인 경우도 DENY로 취급돼야 한다
(`test_execute_action.py::test_gate_decision_missing_compliance_id_denies_even_if_risk_allows`
가 그 축을 단위 테스트로 커버; 여기서는 세 행동 종류(order/hedge/kill) 전부에서
kill-switch-active 시나리오가 동일하게 막히는지를 한 번에 증명한다).
"""

from __future__ import annotations

import time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.automation.application.execute_action import execute_action
from src.foundation.automation.contracts.v1 import Action, HedgeAction, KillAction, OrderAction
from src.foundation.automation.flags import FEATURE_FLAG_NAME
from src.foundation.automation.ports.gate import ActionIntent, GateDecision
from src.foundation.risk_gate.contracts.v1 import RiskOutcome, SafetyScope
from tests.foundation.unit.automation.conftest import FakeGate, FakeNotifier, FakeSink

_ACTIONS: list[Action] = [
    OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
    HedgeAction(symbol="005930", hedge_symbol="KOSPI200F", quantity=Decimal("1")),
    KillAction(scope=SafetyScope.GLOBAL, reason="drawdown breach"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ACTIONS, ids=["order", "hedge", "kill"])
async def test_action_rejected_zero_executions_when_kill_switch_active(action: Action) -> None:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ACTIONS, ids=["order", "hedge", "kill"])
async def test_action_rejected_zero_executions_when_feature_flag_off(
    action: Action, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative test: `FF_U4A_RULE_ENGINE` off must block every action kind
    before the gate is even consulted (execute_action.py docstring) -- the
    gate must never be called, not just the sink."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "0")
    sink = FakeSink()
    gate = FakeGate(allow=True)
    result = await execute_action(
        tenant_id=uuid4(),
        rule_id=uuid4(),
        action=action,
        trace_id=uuid4(),
        gate=gate,
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.detail == "feature_disabled"
    assert sink.total_calls == 0
    assert gate.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ACTIONS, ids=["order", "hedge", "kill"])
async def test_action_rejected_when_risk_allows_but_compliance_denies(action: Action) -> None:
    """Negative test + INVARIANTS.md I-09 cross-check: a one-sided ALLOW
    (risk ALLOW, compliance DENY) must still be a zero-execution DENY -- the
    final decision requires both axes, not just one (GateDecision.allowed)."""

    class _MixedGate:
        def __init__(self) -> None:
            self.calls: list[ActionIntent] = []

        async def check(self, intent: ActionIntent) -> GateDecision:
            self.calls.append(intent)
            return GateDecision(
                risk_outcome=RiskOutcome.ALLOW,
                risk_decision_id=uuid4(),
                compliance_outcome=RiskOutcome.DENY,
                compliance_decision_id=None,
                reason_codes=("COMPLIANCE_BUNDLE_DENIED",),
            )

    sink = FakeSink()
    result = await execute_action(
        tenant_id=uuid4(),
        rule_id=uuid4(),
        action=action,
        trace_id=uuid4(),
        gate=_MixedGate(),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.detail == "gate_denied"
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_action_rejected_when_gate_reports_allow_without_decision_id() -> None:
    """Negative test: ALLOW outcomes with a missing decision id (evidence
    gap) must fail closed exactly like an explicit DENY -- GateDecision.allowed
    requires both decision ids to be present, not just both outcomes ALLOW."""

    class _MissingRiskDecisionIdGate:
        async def check(self, intent: ActionIntent) -> GateDecision:
            return GateDecision(
                risk_outcome=RiskOutcome.ALLOW,
                risk_decision_id=None,
                compliance_outcome=RiskOutcome.ALLOW,
                compliance_decision_id=uuid4(),
            )

    gate = _MissingRiskDecisionIdGate()
    sink = FakeSink()
    result = await execute_action(
        tenant_id=uuid4(),
        rule_id=uuid4(),
        action=_ACTIONS[0],
        trace_id=uuid4(),
        gate=gate,
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.detail == "gate_denied"
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_gate_check_failure_injection_propagates_without_touching_sink() -> None:
    """Failure injection: if the gate dependency itself raises (e.g. a DB
    timeout evaluating the risk/compliance bundle), execute_action must not
    swallow the error into a fake ALLOW -- it propagates, and crucially the
    sink is never reached (fail-closed on gate infrastructure failure, not
    just on an explicit DENY verdict)."""

    class _ExplodingGate:
        async def check(self, intent: ActionIntent) -> GateDecision:
            raise RuntimeError("risk_gate_unavailable")

    sink = FakeSink()
    with pytest.raises(RuntimeError, match="risk_gate_unavailable"):
        await execute_action(
            tenant_id=uuid4(),
            rule_id=uuid4(),
            action=_ACTIONS[2],
            trace_id=uuid4(),
            gate=_ExplodingGate(),
            sink=sink,
            notifier=FakeNotifier(),
        )
    assert sink.total_calls == 0


@pytest.mark.perf
@pytest.mark.asyncio
async def test_kill_switch_denial_path_meets_latency_budget() -> None:
    """Numeric performance assertion: the deny-path (kill-switch active) for
    all three live-trading action kinds must resolve well under a
    100ms/call budget with an in-memory fake gate -- a regression here would
    mean an accidental blocking I/O call landed on the fail-closed path."""
    sink = FakeSink()
    gate = FakeGate(allow=False, reason="RISK_KILL_SWITCH_ACTIVE_GLOBAL")
    notifier = FakeNotifier()
    tenant_id: UUID = uuid4()

    start = time.perf_counter()
    for action in _ACTIONS:
        result = await execute_action(
            tenant_id=tenant_id,
            rule_id=uuid4(),
            action=action,
            trace_id=uuid4(),
            gate=gate,
            sink=sink,
            notifier=notifier,
        )
        assert result.executed is False
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1 * len(_ACTIONS)
    assert sink.total_calls == 0
