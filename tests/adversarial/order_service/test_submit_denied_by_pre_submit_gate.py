"""task-1762 — `submit.py`(실제 배포 경로)의 pre_submit_gate 거부 배선을
적대적으로 증명한다.

감사 2026-09-06 P2 — 같은 계약(R-37)의 `fenced_submit.py`는
`tests/integration/risk/test_fenced_submit.py`(test_missing_decision_id_
is_fail_closed 등)로 이미 검증됐지만, 실제 배포 경로인 `submit.py`에는
동등한 적대적 테스트가 없었다. `submit.py`는 `gate.py`와 마찬가지로
foundation을 모르는 순수 계약(`PreSubmitGate` 콜러블)만 신뢰하므로, 이
파일은 그 계약 경계에서(가짜 게이트로 "결정 없음"/"만료된 결정"을 직접
구성) 검증하고, 킬스위치 시나리오만 실제 프로덕션 게이트
(`make_foundation_pre_submit_gate`)로 배선까지 관통해 증명한다.
"""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.adversarial.risk.conftest import RecordingAdapter, make_order, seed_execution
from tests.integration.conftest import create_test_tenant


async def _count_by_client_id(pool: asyncpg.Pool, client_order_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM orders WHERE client_order_id = $1", client_order_id
        )


def _static_gate(decision: GateDecision):
    async def gate(_context) -> GateDecision:
        return decision

    return gate


async def test_missing_risk_decision_id_denies_submit_and_no_adapter_call(
    pool: asyncpg.Pool,
) -> None:
    """negative — 게이트가 "결정 없음"(risk_decision_id=None)으로 DENY하면
    submit.py 경로는 거부하고 거래소를 전혀 부르지 않는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    denied = GateDecision(
        outcome=GateOutcome.DENY, reason_codes=("RISK_DECISION_MISSING",), decision_id=None,
    )

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool,
            pre_submit_gate=_static_gate(denied),
        )

    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_expired_decision_denies_submit_and_no_adapter_call(pool: asyncpg.Pool) -> None:
    """negative — 게이트가 만료된 결정으로 DENY하면 submit.py 경로는 거부하고
    거래소를 전혀 부르지 않는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()
    denied = GateDecision(
        outcome=GateOutcome.DENY, reason_codes=("RISK_DECISION_EXPIRED",), decision_id=uuid4(),
    )

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool,
            pre_submit_gate=_static_gate(denied),
        )

    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_active_kill_switch_denies_submit_via_real_production_gate(
    pool: asyncpg.Pool,
) -> None:
    """킬스위치(ACCOUNT scope) 활성 상태에서 실제 프로덕션 게이트 조립부
    (`make_foundation_pre_submit_gate`)를 그대로 주입한 submit.py 경로가
    거부되고 거래소를 전혀 부르지 않음을 증명한다(우회불가능성, I-10)."""
    user_id = await create_test_tenant(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    adapter = RecordingAdapter()

    await activate_safety_control(
        PostgresRiskGateRepository(pool),
        tenant_id=user_id,
        actor_subject_id=user_id,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(user_id),
        reason="task-1762 적대적 테스트 — submit.py 킬스위치 배선",
    )

    gate = make_foundation_pre_submit_gate(pool, require_mandate=True)

    with pytest.raises(OrderDeniedByRiskGateError) as excinfo:
        await submit_order(
            order, user_id=user_id, adapter=adapter, pool=pool, pre_submit_gate=gate,
        )

    assert any("RISK_KILL_SWITCH_ACTIVE" in code for code in excinfo.value.reason_codes)
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0
