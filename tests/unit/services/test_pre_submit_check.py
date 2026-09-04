"""tick.py의 pre_submit_gate 통합 지점 단위테스트 — DB 없음."""
from __future__ import annotations

from uuid import uuid4

from src.services.execution_loop.pre_submit_check import is_submission_allowed
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext


async def test_no_gate_configured_always_allows():
    allowed = await is_submission_allowed(
        None, user_id=uuid4(), execution_id=1, exchange="bitget"
    )
    assert allowed is True


async def test_allow_decision_permits_submission():
    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(outcome=GateOutcome.ALLOW)

    allowed = await is_submission_allowed(
        gate, user_id=uuid4(), execution_id=1, exchange="bitget"
    )
    assert allowed is True


async def test_deny_decision_blocks_submission():
    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_ACTIVE",))

    allowed = await is_submission_allowed(
        gate, user_id=uuid4(), execution_id=1, exchange="bitget"
    )
    assert allowed is False


async def test_gate_receives_correct_order_context():
    user_id = uuid4()
    seen: list[OrderContext] = []

    async def gate(context: OrderContext) -> GateDecision:
        seen.append(context)
        return GateDecision(outcome=GateOutcome.ALLOW)

    await is_submission_allowed(gate, user_id=user_id, execution_id=42, exchange="kis")

    assert len(seen) == 1
    assert seen[0].user_id == user_id
    assert seen[0].execution_id == 42
    assert seen[0].exchange == "kis"
    assert seen[0].mandate_revision_id is None
    assert seen[0].observed_fence is None


async def test_gate_receives_observed_fence_when_provided():
    seen: list[OrderContext] = []

    async def gate(context: OrderContext) -> GateDecision:
        seen.append(context)
        return GateDecision(outcome=GateOutcome.ALLOW)

    await is_submission_allowed(
        gate,
        user_id=uuid4(),
        execution_id=1,
        exchange="bitget",
        observed_fence={"GLOBAL:": 1},
    )

    assert seen[0].observed_fence == {"GLOBAL:": 1}
