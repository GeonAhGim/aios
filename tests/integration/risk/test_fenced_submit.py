"""R-37 `fenced_submit.submit_with_fence` 통합테스트 — 실제 TEST_DATABASE_URL.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §2.7 `test_fenced_submit.py`,
§3.6 시퀀스 2~10, §4.1 I1/I4, §6 두 fence 행. 경합·트리거는
`tests/adversarial/risk/test_fence_race.py`.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import asyncpg
import pytest

from src.core.observability.metric_names import SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL
from src.core.risk.decision import RiskOutcome
from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.fenced_submit import (
    AUDIT_FENCE_STALE_PREVENTED,
    AUDIT_POST_FENCE_DETECTED,
    FenceStaleError,
    RiskDecisionMissingError,
    stale_pairs,
    submit_with_fence,
)
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.order_service.submit import OrderDeniedByRiskGateError
from tests.adversarial.risk import conftest as _fixtures
from tests.adversarial.risk.conftest import (
    RecordingAdapter,
    SpyMetrics,
    audit_count,
    fence_reader,
    insert_decision,
    make_order,
    order_row,
    seed_execution,
)
from tests.integration.conftest import create_test_user

pool = _fixtures.pool  # 픽스처 re-export(execution_ownership conftest 관례)

_METRIC = SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL


@pytest.fixture
async def ctx(pool):
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    read = fence_reader(pool, user_id, execution_id)
    f0 = await read()
    gate = GateDecision(
        outcome=GateOutcome.ALLOW, fence_snapshot=f0, decision_id=decision.decision_id
    )
    return {"user_id": user_id, "execution_id": execution_id, "decision": decision,
            "f0": f0, "read": read, "gate": gate}


def _allow(ctx, decision_id) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, fence_snapshot=ctx["f0"], decision_id=decision_id
    )


# tenant/실행 스코프만 쓴다 — GLOBAL/PROVIDER 통제는 공유 테스트 DB의 다른
# 테스트(예: test_order_service_risk_gate.py)까지 DENY로 오염시킨다.
_ISOLATED_SCOPES = (SafetyScope.STRATEGY_DEPLOYMENT, SafetyScope.TENANT, SafetyScope.ACCOUNT)


async def _activate_scope(pool, ctx, scope: SafetyScope) -> None:
    ref = {SafetyScope.TENANT: str(ctx["user_id"]), SafetyScope.ACCOUNT: str(ctx["user_id"]),
           SafetyScope.STRATEGY_DEPLOYMENT: f"exec:{ctx['execution_id']}"}[scope]
    await activate_safety_control(
        PostgresRiskGateRepository(pool), tenant_id=ctx["user_id"],
        actor_subject_id=ctx["user_id"], actor_is_admin=True, scope=scope, scope_ref=ref,
        reason="fenced-submit-test", trace_id=uuid4(),
    )


async def _count_by_client_id(pool, client_order_id: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM orders WHERE client_order_id = $1", client_order_id
        )


async def _submit(pool, ctx, adapter, order, *, gate=None, read=None, metrics=None) -> Order:
    return await submit_with_fence(
        pool, adapter, order, user_id=ctx["user_id"], gate_decision=gate or ctx["gate"],
        read_fences=read or ctx["read"], decision_reader=PostgresDecisionRepository(pool),
        metrics=metrics,
    )


def test_stale_pairs_only_counts_increases() -> None:
    assert stale_pairs({"GLOBAL:": 1, "TENANT:t": 2}, {"GLOBAL:": 1, "TENANT:t": 2}) == ()
    assert stale_pairs({"GLOBAL:": 1}, {"GLOBAL:": 2, "PROVIDER:bitget": 0}) == ("GLOBAL:",)
    assert stale_pairs({}, {"PROVIDER:bitget": 1, "GLOBAL:": 3}) == ("GLOBAL:", "PROVIDER:bitget")
    assert stale_pairs({"GLOBAL:": 5}, {"GLOBAL:": 4}) == ()  # 감소는 stale 아님(DB 제약상 불가)


async def test_denied_gate_raises_before_claim(pool, ctx):
    adapter = RecordingAdapter()
    order = make_order(ctx["execution_id"])
    denied = GateDecision(
        outcome=GateOutcome.DENY, reason_codes=("RISK_KILL_SWITCH_ACTIVE_GLOBAL",),
        fence_snapshot=ctx["f0"], decision_id=ctx["decision"].decision_id,
    )
    with pytest.raises(OrderDeniedByRiskGateError):
        await _submit(pool, ctx, adapter, order, gate=denied)
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_missing_decision_id_is_fail_closed(pool, ctx):
    """negative — I1: 결정 참조 없는 ALLOW는 claim조차 못 한다."""
    adapter = RecordingAdapter()
    order = make_order(ctx["execution_id"])
    with pytest.raises(RiskDecisionMissingError):
        await _submit(pool, ctx, adapter, order, gate=_allow(ctx, None))
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_forged_allow_with_non_actionable_decision_rejected_by_trigger(pool, ctx):
    """negative — 코드가 DENY 결정 id로 ALLOW를 위조해도 DB 트리거가 claim을 막는다."""
    deny = await insert_decision(
        pool, ctx["user_id"], outcome=RiskOutcome.DENY, execution_ref=f"exec:{ctx['execution_id']}"
    )
    adapter = RecordingAdapter()
    order = make_order(ctx["execution_id"])
    with pytest.raises(asyncpg.CheckViolationError):
        await _submit(pool, ctx, adapter, order, gate=_allow(ctx, deny.decision_id))
    assert adapter.place_order_call_count == 0
    assert await _count_by_client_id(pool, order.client_order_id) == 0


@pytest.mark.parametrize("scope", _ISOLATED_SCOPES)
async def test_stale_fence_before_submit_fails_claim_and_audits(pool, ctx, scope):
    await _activate_scope(pool, ctx, scope)  # F0 관측 뒤, 제출 전 fence 증가
    adapter = RecordingAdapter()
    metrics = SpyMetrics()
    with pytest.raises(FenceStaleError) as excinfo:
        await _submit(pool, ctx, adapter, make_order(ctx["execution_id"]), metrics=metrics)
    order_id = excinfo.value.order_id
    assert adapter.place_order_call_count == 0
    row = await order_row(pool, order_id)
    assert (row["status"], row["risk_decision_id"]) == ("FAILED", ctx["decision"].decision_id)
    assert await audit_count(pool, AUDIT_FENCE_STALE_PREVENTED, order_id) == 1
    assert metrics.counters.get(_METRIC, 0) == 0
    assert excinfo.value.stale_pairs[0].startswith(scope.value)


async def test_happy_path_persists_decision_reference(pool, ctx):
    adapter = RecordingAdapter()
    metrics = SpyMetrics()
    persisted = await _submit(pool, ctx, adapter, make_order(ctx["execution_id"]), metrics=metrics)
    assert persisted.status == OrderStatus.SUBMITTED
    row = await order_row(pool, persisted.order_id)
    assert (row["status"], row["risk_decision_id"]) == ("SUBMITTED", ctx["decision"].decision_id)
    assert metrics.counters.get(_METRIC, 0) == 0
    assert adapter.cancelled_exchange_order_ids == []


async def test_post_fence_during_place_is_counted_cancelled_and_audited(pool, ctx):
    """§6 "어댑터 호출 후 fence 변경(진짜 post-fence)" — 막지 못한 창을 F2가 잡는다."""

    async def hook(order: Order) -> Order:
        await _activate_scope(pool, ctx, SafetyScope.STRATEGY_DEPLOYMENT)  # F1~F2 사이
        return order.model_copy(
            update={"exchange_order_id": "ex-post-fence", "status": OrderStatus.SUBMITTED}
        )

    adapter = RecordingAdapter(on_place_order=hook)
    metrics = SpyMetrics()
    persisted = await _submit(pool, ctx, adapter, make_order(ctx["execution_id"]), metrics=metrics)
    assert metrics.counters[_METRIC] == 1
    assert adapter.cancelled_exchange_order_ids == ["ex-post-fence"]
    assert persisted.status == OrderStatus.CANCELLED
    assert (await order_row(pool, persisted.order_id))["status"] == "CANCELLED"
    assert await audit_count(pool, AUDIT_POST_FENCE_DETECTED, persisted.order_id) == 1


async def test_duplicate_client_order_id_returns_existing_without_second_place(pool, ctx):
    adapter = RecordingAdapter()
    order = make_order(ctx["execution_id"])
    first = await _submit(pool, ctx, adapter, order)
    second = await _submit(pool, ctx, adapter, order)
    assert (first.order_id, second.order_id) == (order.order_id, order.order_id)
    assert adapter.place_order_call_count == 1
    assert await _count_by_client_id(pool, order.client_order_id) == 1


async def test_adapter_error_deletes_claim(pool, ctx):
    async def boom(order: Order) -> Order:
        raise RuntimeError("network")

    adapter = RecordingAdapter(on_place_order=boom)
    order = make_order(ctx["execution_id"])
    with pytest.raises(RuntimeError):
        await _submit(pool, ctx, adapter, order)
    assert await _count_by_client_id(pool, order.client_order_id) == 0


async def test_expired_decision_reference_rejected_at_claim(pool, ctx):
    """negative — 만료된 결정으로는 claim 불가(트리거 `created_at < expires_at`)."""
    expired = await insert_decision(
        pool, ctx["user_id"], ttl=timedelta(seconds=-1),
        execution_ref=f"exec:{ctx['execution_id']}",
    )
    adapter = RecordingAdapter()
    gate = _allow(ctx, expired.decision_id)
    with pytest.raises(asyncpg.CheckViolationError, match="RISK_DECISION_EXPIRED"):
        await _submit(pool, ctx, adapter, make_order(ctx["execution_id"]), gate=gate)
    assert adapter.place_order_call_count == 0
