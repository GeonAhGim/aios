"""R-56 적대적 — 재현(needs_decision): 같은 tenant의 유효 ALLOW 결정을 다른
subject(실행·심볼·수량)의 주문에 이전할 수 있는가(I10), 위조 F0로 fence를
무력화할 수 있는가(I4).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §4.1 I1("fingerprint 일치"),
I4("관측 스냅샷과 현재가 다르면 부작용 금지"), I10("ALLOW는 한 subject 전용
… 다른 intent에 이전 불가 — 위반 시 409"), §8 "RSK-006 … fingerprint 불일치
409".

이 파일은 task-1520 decision("src 수정이 필요한 실결함은 고치지 말고
needs_decision으로 재현 테스트와 함께")에 따라 **의도적으로 현재 코드에서
실패하는** 재현이다 — xfail/skip 같은 지연 단언을 쓰지 않는다. main에는
올리지 않고 wt/backend-4 브랜치 커밋으로만 보존한다(CI 보호).

현재 관찰(2026-09-05, 62aa2a9 기준):
- 트리거 `orders_require_risk_decision`은 tenant·outcome·만료만 검사하고
  `execution_ref`↔`orders.execution_id`·intent(symbol/quantity)를 비교하지
  않는다. `fenced_submit`도 `decision_id`만 넘긴다.
- `fenced_submit`의 F0는 호출자가 준 `GateDecision.fence_snapshot`을 그대로
  믿는다. WORM `inputs_snapshot.fence_snapshot`과 대조하지 않는다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.fenced_submit import FenceStaleError, submit_with_fence
from src.services.order_service.gate import GateDecision, GateOutcome
from tests.adversarial.risk.conftest import (
    RecordingAdapter,
    fence_reader,
    insert_decision,
    make_order,
    seed_execution,
)
from tests.integration.conftest import create_test_user


async def test_i10_allow_for_execution_x_cannot_be_transferred_to_execution_y(pool):
    """결정은 exec:X·BTC/USDT·0.01 subject에 대한 ALLOW. 공격자는 같은 tenant의
    exec:Y·ETH/USDT·100 주문에 그 decision_id를 붙인다. 기대: 거부(409류)."""
    user_id = await create_test_user(pool)
    exec_x = await seed_execution(pool, user_id)
    exec_y = await seed_execution(pool, user_id)
    decision_for_x = await insert_decision(pool, user_id, execution_ref=f"exec:{exec_x}")
    read = fence_reader(pool, user_id, exec_y)
    gate = GateDecision(
        outcome=GateOutcome.ALLOW, fence_snapshot=await read(),
        decision_id=decision_for_x.decision_id,
    )
    foreign_order = make_order(exec_y).model_copy(
        update={"symbol": "ETH/USDT", "quantity": Decimal("100")}
    )
    adapter = RecordingAdapter()

    with pytest.raises((asyncpg.CheckViolationError, Exception)) as exc_info:
        await submit_with_fence(
            pool, adapter, foreign_order, user_id=user_id, gate_decision=gate, read_fences=read
        )
    assert adapter.place_order_call_count == 0, "타 subject 주문이 거래소에 도달(I10 위반)"
    assert "FINGERPRINT" in str(exc_info.value).upper()


async def test_i4_forged_f0_cannot_bypass_fence_after_kill_switch(pool):
    """유효 ALLOW를 얻은 뒤 kill switch가 걸린다. 공격자는 F0를 부풀려
    `stale_pairs(F0, F1)`가 비도록 만든다. 기대: FenceStaleError, 어댑터 0."""
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    read = fence_reader(pool, user_id, execution_id)
    honest_f0 = await read()

    await activate_safety_control(
        PostgresRiskGateRepository(pool), tenant_id=user_id, actor_subject_id=user_id,
        actor_is_admin=True, scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"exec:{execution_id}", reason="i4-repro", trace_id=uuid4(),
    )
    assert (await read()) != honest_f0  # fence는 실제로 움직였다

    forged = GateDecision(
        outcome=GateOutcome.ALLOW, fence_snapshot={k: 2**40 for k in honest_f0},
        decision_id=decision.decision_id,
    )
    adapter = RecordingAdapter()
    with pytest.raises(FenceStaleError):
        await submit_with_fence(
            pool, adapter, make_order(execution_id), user_id=user_id,
            gate_decision=forged, read_fences=read,
        )
    assert adapter.place_order_call_count == 0, "kill switch 이후 위조 F0로 거래소 도달(I4 위반)"
