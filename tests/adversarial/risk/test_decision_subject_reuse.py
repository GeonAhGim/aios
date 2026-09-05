"""R-56 적대적 — I10 결정 subject 이전·I4 위조 F0 fence 우회 (task-1520 재현
→ task-1532 수정 실증).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.6 2~3단계, §4.1 I1("fingerprint
일치"), I4("관측 스냅샷과 현재가 다르면 부작용 금지"), I10("ALLOW는 한
subject 전용 … 다른 intent에 이전 불가 — 위반 시 409"), §8 "RSK-006 …
fingerprint 불일치 409". docs/design/INVARIANTS.md I-01(Optional 게이트 인자
금지)·I-10(배선 증명).

task-1520(51be3c7, repro 774a0a0)이 재현한 두 결함 — 트리거는 tenant·outcome·
만료만 보고 `fenced_submit`은 호출자 F0를 그대로 믿었다 — 를 task-1532가
`worm_decision_check.bind_to_worm_decision`(claim 전 WORM 재조회·결속 대조)
으로 막는다. 이 파일은 그 수정 전 코드에서 두 테스트가 실패했음을 커밋
메시지에 기록하고(xfail/skip 같은 지연 단언 없음), 모든 케이스에서
(a) 거래소 어댑터 호출 0, (b) `orders` 행 0(claim 없음), (c) 감사 행
`risk_decision_integrity_rejected`를 단언한다.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.data.models.trading import Order, OrderSide
from src.foundation.connections.domain.models import HealthState
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.fenced_submit import FenceStaleError, submit_with_fence
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.worm_decision_check import (
    AUDIT_DECISION_INTEGRITY_REJECTED,
    RiskDecisionIntegrityError,
)
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.adversarial.risk.conftest import (
    RecordingAdapter,
    audit_count,
    fence_reader,
    insert_decision,
    make_order,
    recorded_inputs,
    seed_execution,
)
from tests.integration.conftest import NoopEventBus, create_test_user
from tests.integration.risk.test_pre_submit_gate import (
    _FakeConnectionRepo,
    _RiskRepoWithFixedSafetyState,
)


def _allow(decision_id: UUID, f0: dict[str, int]) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW, fence_snapshot=f0, decision_id=decision_id)


async def _orders_for(pool: asyncpg.Pool, user_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", user_id)


async def _reject(
    pool: asyncpg.Pool, *, user_id: UUID, execution_id: int, order: Order, gate: GateDecision
) -> RiskDecisionIntegrityError:
    """결속 불일치 제출: claim 전 거부·어댑터 0·orders 0·감사 1을 단언하고 예외를 돌려준다."""
    adapter = RecordingAdapter()
    before = await _orders_for(pool, user_id)
    assert gate.decision_id is not None
    audits_before = await audit_count(pool, AUDIT_DECISION_INTEGRITY_REJECTED, gate.decision_id)
    with pytest.raises(RiskDecisionIntegrityError) as exc_info:
        await submit_with_fence(
            pool, adapter, order, user_id=user_id, gate_decision=gate,
            read_fences=fence_reader(pool, user_id, execution_id),
            decision_reader=PostgresDecisionRepository(pool),
        )
    assert adapter.place_order_call_count == 0, "결속 불일치 주문이 거래소에 도달(I10/I4 위반)"
    assert await _orders_for(pool, user_id) == before, "결속 불일치 주문이 claim됐다"
    assert "INTEGRITY_RISK_FINGERPRINT_MISMATCH" in str(exc_info.value)
    after = await audit_count(pool, AUDIT_DECISION_INTEGRITY_REJECTED, gate.decision_id)
    assert after == audits_before + 1
    return exc_info.value


@pytest.fixture
async def victim(pool: asyncpg.Pool) -> dict[str, Any]:
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    read = fence_reader(pool, user_id, execution_id)
    f0 = dict(await read())
    return {"user_id": user_id, "execution_id": execution_id, "decision": decision,
            "f0": f0, "read": read}


# --- I10: 한 subject의 ALLOW는 다른 subject로 이전되지 않는다 -----------------


async def test_i10_allow_for_execution_x_cannot_be_transferred_to_execution_y(pool, victim):
    """결정은 exec:X·BTC/USDT·BUY·0.01 subject에 대한 ALLOW. 공격자는 같은 tenant의
    exec:Y·ETH/USDT·100 주문에 그 decision_id를 붙인다(task-1520 재현 원문)."""
    exec_y = await seed_execution(pool, victim["user_id"])
    foreign = make_order(exec_y).model_copy(
        update={"symbol": "ETH/USDT", "quantity": Decimal("100")}
    )
    f0_for_y = dict(await fence_reader(pool, victim["user_id"], exec_y)())
    error = await _reject(
        pool, user_id=victim["user_id"], execution_id=exec_y, order=foreign,
        gate=_allow(victim["decision"].decision_id, f0_for_y),
    )
    assert set(error.mismatches) >= {"execution_ref", "symbol", "quantity"}


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"symbol": "ETH/USDT"}, ("symbol",)),
        ({"side": OrderSide.SELL}, ("side",)),
        ({"quantity": Decimal("0.02")}, ("quantity",)),
    ],
    ids=["symbol", "side", "quantity"],
)
async def test_i10_same_execution_different_intent_field_is_rejected(
    pool, victim, update, expected
):
    """execution·tenant·F0는 전부 맞고 intent 한 필드만 다르다 — 그 한 필드만으로 거부."""
    order = make_order(victim["execution_id"]).model_copy(update=update)
    error = await _reject(
        pool, user_id=victim["user_id"], execution_id=victim["execution_id"], order=order,
        gate=_allow(victim["decision"].decision_id, victim["f0"]),
    )
    assert error.mismatches == expected


@pytest.mark.parametrize(
    ("drop", "expected"),
    [
        (("side", "quantity"), ("side", "quantity")),
        (("fence_snapshot",), ("fence_snapshot_missing",)),
    ],
    ids=["intent_not_recorded", "fence_not_recorded"],
)
async def test_decision_without_binding_keys_in_worm_is_fail_closed(pool, victim, drop, expected):
    """negative — R-35 이전 형식(intent·fence 미기록) 행은 결속을 증명할 수 없으므로 거부."""
    ref = f"exec:{victim['execution_id']}"
    snapshot = await recorded_inputs(pool, victim["user_id"], execution_ref=ref)
    for key in drop:
        snapshot.pop(key)
    legacy = await insert_decision(
        pool, victim["user_id"], execution_ref=ref, inputs_snapshot=snapshot
    )
    error = await _reject(
        pool, user_id=victim["user_id"], execution_id=victim["execution_id"],
        order=make_order(victim["execution_id"]), gate=_allow(legacy.decision_id, victim["f0"]),
    )
    assert error.mismatches == expected


# --- I4: 호출자 F0는 WORM F0와 같아야 한다 ------------------------------------


async def _kill_switch(pool: asyncpg.Pool, victim: dict[str, Any]) -> None:
    await activate_safety_control(
        PostgresRiskGateRepository(pool), tenant_id=victim["user_id"],
        actor_subject_id=victim["user_id"], actor_is_admin=True,
        scope=SafetyScope.STRATEGY_DEPLOYMENT, scope_ref=f"exec:{victim['execution_id']}",
        reason="i4-repro", trace_id=uuid4(),
    )


async def test_i4_forged_f0_cannot_bypass_fence_after_kill_switch(pool, victim):
    """유효 ALLOW를 얻은 뒤 kill switch가 걸린다. 공격자는 F0를 부풀려
    `stale_pairs(F0, F1)`가 비도록 만든다(task-1520 재현 원문). 기대: F0가 WORM과
    다르다는 이유로 claim 전 거부 — fence 비교까지 가지도 않는다."""
    await _kill_switch(pool, victim)
    assert dict(await victim["read"]()) != victim["f0"]  # fence는 실제로 움직였다
    forged = {k: 2**40 for k in victim["f0"]}
    error = await _reject(
        pool, user_id=victim["user_id"], execution_id=victim["execution_id"],
        order=make_order(victim["execution_id"]),
        gate=_allow(victim["decision"].decision_id, forged),
    )
    assert error.mismatches == ("fence_snapshot",)


async def test_i4_understated_f0_is_also_rejected_not_silently_replaced(pool, victim):
    """F0가 WORM보다 *낮아도* 거부 — 비교는 등호이고, 다르면 위조 증거로 감사한다."""
    understated = dict(victim["f0"])
    understated[f"STRATEGY_DEPLOYMENT:exec:{victim['execution_id']}"] = -1
    error = await _reject(
        pool, user_id=victim["user_id"], execution_id=victim["execution_id"],
        order=make_order(victim["execution_id"]),
        gate=_allow(victim["decision"].decision_id, understated),
    )
    assert error.mismatches == ("fence_snapshot",)


async def test_i4_control_honest_f0_after_kill_switch_is_fence_stale(pool, victim):
    """대조군 — 정직한 F0면 결속은 통과하고 §3.6 5~6 fence 비교가 막는다(어댑터 0)."""
    await _kill_switch(pool, victim)
    adapter = RecordingAdapter()
    with pytest.raises(FenceStaleError):
        await submit_with_fence(
            pool, adapter, make_order(victim["execution_id"]), user_id=victim["user_id"],
            gate_decision=_allow(victim["decision"].decision_id, victim["f0"]),
            read_fences=victim["read"], decision_reader=PostgresDecisionRepository(pool),
        )
    assert adapter.place_order_call_count == 0


# --- I-10 배선 증명 --------------------------------------------------------------


def test_i01_submit_with_fence_gate_inputs_have_no_defaults():
    """정적 — `decision_reader`(WORM 재조회)와 `read_fences`·`gate_decision`은
    keyword-only·기본값 없음. 기본값이 생기면 그 자체가 우회 경로다."""
    params = inspect.signature(submit_with_fence).parameters
    for name in ("gate_decision", "read_fences", "decision_reader"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is inspect.Parameter.empty, name
    reader = inspect.signature(PostgresDecisionRepository.get_for_tenant).parameters
    assert tuple(reader) == ("self", "decision_id", "tenant_id")


async def test_i10_wiring_real_pre_submit_decision_binds_only_to_its_order(pool):
    """R-35 `evaluate_pre_submit`이 실제로 기록한 WORM 행 → `submit_with_fence`.
    같은 결정이 수량만 다른 주문엔 거부되고, 원래 주문엔 통과한다(TTL 2s 안)."""
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    order = make_order(execution_id)
    decision, fence = await evaluate_pre_submit(
        _RiskRepoWithFixedSafetyState(
            PostgresRiskGateRepository(pool), cb_level="normal", distrust_level="NORMAL"
        ),
        _FakeConnectionRepo(tenant_id=user_id, provider_code="bitget", health=HealthState.HEALTHY),
        RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus()),
        tenant_id=user_id, execution_ref=f"exec:{execution_id}", provider_code="bitget",
        symbol=order.symbol, side=order.side.value, quantity=order.quantity, trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.ALLOW
    f0 = {f"{scope.value}:{ref}": token for (scope, ref), token in fence.tokens.items()}
    gate = _allow(decision.decision_id, f0)

    other = make_order(execution_id).model_copy(update={"quantity": Decimal("0.02")})
    error = await _reject(pool, user_id=user_id, execution_id=execution_id, order=other, gate=gate)
    assert error.mismatches == ("quantity",)

    adapter = RecordingAdapter()
    submitted = await submit_with_fence(
        pool, adapter, order, user_id=user_id, gate_decision=gate,
        read_fences=fence_reader(pool, user_id, execution_id),
        decision_reader=PostgresDecisionRepository(pool),
    )
    assert adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT risk_decision_id FROM orders WHERE order_id = $1", submitted.order_id
        )
    assert row is not None and row["risk_decision_id"] == decision.decision_id
