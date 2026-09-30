"""tick.py의 pre_submit_gate 통합 지점 단위테스트 — DB 없음."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from src.services.execution_loop.pre_submit_check import is_submission_allowed
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext


async def test_missing_gate_fails_closed():
    """task-1715(P0-B) — `pre_submit_gate`는 필수 인자다(I-01). None을
    억지로 넘기면(정적 검사 우회) 무조건 허용하던 예전 fail-open 대신
    호출 자체가 터진다(게이트가 아닌 값을 호출하려다 TypeError)."""
    with pytest.raises(TypeError):
        await is_submission_allowed(
            cast(Any, None),
            user_id=uuid4(),
            execution_id=1,
            exchange="bitget",
        )


async def test_allow_decision_permits_submission():
    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
        )

    allowed = await is_submission_allowed(gate, user_id=uuid4(), execution_id=1, exchange="bitget")
    assert allowed is True


async def test_deny_decision_blocks_submission():
    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_ACTIVE",))

    allowed = await is_submission_allowed(gate, user_id=uuid4(), execution_id=1, exchange="bitget")
    assert allowed is False


async def test_gate_receives_correct_order_context():
    user_id = uuid4()
    seen: list[OrderContext] = []

    async def gate(context: OrderContext) -> GateDecision:
        seen.append(context)
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
        )

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
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
        )

    await is_submission_allowed(
        gate,
        user_id=uuid4(),
        execution_id=1,
        exchange="bitget",
        observed_fence={"GLOBAL:": 1},
    )

    assert seen[0].observed_fence == {"GLOBAL:": 1}


async def test_deny_without_reason_codes_still_blocks_submission():
    """불변식 위반 입력: `reason_codes`가 비어 있어도(기본 `()`) outcome이
    DENY면 반드시 차단해야 한다 — 로그 문자열용 reason_codes 유무로 허용
    여부를 판단하는 회귀를 막는다(§0 fail-closed 원칙)."""

    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(outcome=GateOutcome.DENY)

    allowed = await is_submission_allowed(gate, user_id=uuid4(), execution_id=1, exchange="bitget")
    assert allowed is False


async def test_deny_with_decision_ids_present_still_blocks_submission():
    """불변식 위반 입력: `decision_id`/`compliance_decision_id`가 채워져
    있어도 outcome이 DENY면 그 필드들은 허용 여부에 영향을 주지 않는다 —
    `is_submission_allowed`는 오직 `outcome == ALLOW`만 판단 기준으로
    삼아야 한다(다른 필드 존재 여부로 fail-open 되는 회귀 방지)."""

    async def gate(context: OrderContext) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.DENY,
            reason_codes=("RISK_KILL_SWITCH_ACTIVE",),
            decision_id=uuid4(),
            compliance_decision_id=uuid4(),
        )

    allowed = await is_submission_allowed(gate, user_id=uuid4(), execution_id=1, exchange="bitget")
    assert allowed is False


async def test_gate_exception_propagates_instead_of_fail_open(monkeypatch: pytest.MonkeyPatch):
    """실패주입: 의존성(게이트 콜러블)이 평가 도중 예외를 던지면
    `is_submission_allowed`는 그 예외를 삼켜 허용으로 되돌리지 않고 그대로
    전파해야 한다(fail-closed) — tick.py 쪽 호출부가 예외를 캐치해 이번
    tick을 건너뛰는 것이지, 이 함수 내부에서 조용히 True를 반환하면 안 된다."""

    async def failing_gate(context: OrderContext) -> GateDecision:
        raise RuntimeError("foundation_gate dependency unavailable")

    with pytest.raises(RuntimeError, match="foundation_gate dependency unavailable"):
        await is_submission_allowed(
            failing_gate, user_id=uuid4(), execution_id=1, exchange="bitget"
        )
