"""task-2151(L4-18a) 통합테스트 — 만료 execution lease 회수 + 복구 완료
전 submit_order 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6(이 배치는 ①
만료 lease 회수와 ⑤ 복구 완료 전 submit_order 거부만 다룬다 — ②③④는
task-2310), §9 L4-18. DoD: 만료 lease 1건이 심어진 상태로 복구를 돌리면
그 lease가 회수되고, 복구 완료 플래그가 서기 전 submit_order 호출이
구체 에러코드(`OMS_RECOVERY_IN_PROGRESS`)로 거부된다.

task-2794 DEEPEN of task-2151 (docs/audit/DEPTH_L4_BR.md #2151, 실측 D1 <
D3 하한): 원 커밋(b3645f8a)에는 수치 성능 단언, 명시적 gate/CI 회귀
가드, 적대적/다중 인스턴스/재전송 테스트가 없었고 failure-injection은
"죽은 프로세스의 만료 lease 시드"라는 암묵적 형태뿐이었다. 아래를
추가한다: (1) `RecoveryState` 기본값과 예외 처리 분기를 고정하는
gate-red 회귀 가드 2건, (2) `reclaim_expired_leases`를 명시적으로
실패시켜 게이트가 계속 닫혀 있음을 증명하는 failure-injection,
(3) 두 프로세스가 동시에 재시작해 동시에 reclaim하는 다중 인스턴스
경합 테스트, (4) 복구 미완료로 거부된 주문을 복구 완료 후 같은 객체로
재전송(replay)했을 때 정확히 한 번만 성공함을 증명하는 테스트,
(5) `reclaim_expired_leases`가 행 수 증가에도 단일 DELETE 왕복으로
남아있음을 정규화 배율로 단언하는 수치 성능 테스트.
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.execution_loop import recovery_wiring
from src.services.oms.application.restart_recovery import (
    RECOVERY_IN_PROGRESS_REASON,
    RecoveryState,
    make_recovery_gate,
    reclaim_expired_leases,
)
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from src.services.order_service.submit import OrderDeniedByRiskGateError, submit_order
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.foundation.execution_ownership.conftest import create_execution

_INSERT_LEASE_SQL = """
    INSERT INTO execution_leases (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
    VALUES ($1, $2, 0, now() - interval '1 hour', $3)
"""


async def _seed_lease(
    pool: asyncpg.Pool, execution_id: int, *, owner_id: str, expired: bool
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(seconds=30) if expired else now + timedelta(hours=1)
    async with pool.acquire() as conn:
        await conn.execute(_INSERT_LEASE_SQL, execution_id, owner_id, expires_at)


async def _lease_owner(pool: asyncpg.Pool, execution_id: int) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT owner_id FROM execution_leases WHERE execution_id = $1", execution_id
        )


def _make_order(execution_id: int) -> Order:
    return Order(
        client_order_id=f"recovery-gate-{uuid.uuid4().hex}",
        strategy_id="oms-recovery-gate-test",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


async def test_reclaim_expired_leases_deletes_only_expired_rows(pool):
    user_id = await create_test_user(pool)
    expired_exec = await create_execution(pool, user_id)
    live_exec = await create_execution(pool, user_id)
    await _seed_lease(pool, expired_exec, owner_id="dead-process", expired=True)
    await _seed_lease(pool, live_exec, owner_id="alive-process", expired=False)

    reclaimed = await reclaim_expired_leases(pool)

    assert reclaimed >= 1
    assert await _lease_owner(pool, expired_exec) is None
    assert await _lease_owner(pool, live_exec) == "alive-process"


async def test_reclaim_expired_leases_records_audit_log(pool):
    user_id = await create_test_user(pool)
    expired_exec = await create_execution(pool, user_id)
    await _seed_lease(pool, expired_exec, owner_id="dead-process", expired=True)

    await reclaim_expired_leases(pool)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT decision_data FROM audit_log WHERE action_type = $1",
            "system.restart_recovery.leases",
        )
    assert rows


async def test_recovery_gate_denies_before_recovery_complete():
    state = RecoveryState()

    async def delegate(context: OrderContext) -> GateDecision:
        raise AssertionError("복구 미완료 상태에서는 실제 게이트로 위임하면 안 된다")

    gate = make_recovery_gate(state, delegate)
    decision = await gate(
        OrderContext(
            user_id=uuid.uuid4(), execution_id=1, exchange="bitget", mandate_revision_id=None
        )
    )

    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)


async def test_recovery_gate_delegates_after_recovery_complete():
    state = RecoveryState()
    state.mark_complete()
    delegated: list[OrderContext] = []

    async def delegate(context: OrderContext) -> GateDecision:
        delegated.append(context)
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
        )

    gate = make_recovery_gate(state, delegate)
    decision = await gate(
        OrderContext(
            user_id=uuid.uuid4(), execution_id=1, exchange="bitget", mandate_revision_id=None
        )
    )

    assert decision.outcome == GateOutcome.ALLOW
    assert len(delegated) == 1


async def test_submit_order_rejected_with_recovery_reason_before_complete(pool):
    """DoD — 복구 완료 전 submit_order 호출이 구체 에러코드로 거부된다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    state = RecoveryState()

    async def never_delegate(context: OrderContext) -> GateDecision:
        raise AssertionError("게이트가 위임되면 안 된다 — 복구 미완료")

    gate = make_recovery_gate(state, never_delegate)
    order = _make_order(execution_id)

    with pytest.raises(OrderDeniedByRiskGateError) as exc_info:
        await submit_order(
            order,
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=gate,
        )

    assert exc_info.value.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert exists is None


async def test_submit_order_allowed_after_recovery_complete(pool):
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    state = RecoveryState()
    state.mark_complete()

    async def allow_all(context: OrderContext) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
        )

    gate = make_recovery_gate(state, allow_all)
    order = _make_order(execution_id)

    submitted = await submit_order(
        order,
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=gate,
    )

    assert submitted.client_order_id == order.client_order_id


# ---------------------------------------------------------------------------
# task-2794 DEEPEN — gate/CI 회귀 가드
# ---------------------------------------------------------------------------


def test_recovery_state_defaults_to_incomplete_fail_closed():
    """게이트-레드 회귀 가드 — `RecoveryState()`의 기본값이 True로 바뀌면
    `run_startup_recovery`가 한 번도 불리지 않은 프로세스에서도 submit_order가
    즉시 허용돼 I-10(fail-closed)이 깨진다. 생성자 기본값을 직접 고정한다."""
    assert RecoveryState().complete is False


def test_run_startup_recovery_gated_exception_handler_never_marks_complete():
    """게이트-레드 회귀 가드 — `run_startup_recovery_gated`의 except 분기가
    "복구 실패해도 앱은 띄워야 하니 일단 완료로 표시하자"는 식으로 수정되면
    §6 F6 ⑤(복구 완료 전 거부)가 조용히 우회된다. 소스를 스캔해 그 분기
    안에 `mark_complete` 호출이 없음을 고정한다."""
    source = inspect.getsource(recovery_wiring.run_startup_recovery_gated)
    except_block = source.split("except Exception:", 1)[1]
    assert "mark_complete" not in except_block


# ---------------------------------------------------------------------------
# task-2794 DEEPEN — 명시적 failure-injection(만료 lease 시드 외의 축)
# ---------------------------------------------------------------------------


async def test_run_startup_recovery_gated_stays_denied_when_lease_reclaim_raises(pool, monkeypatch):
    """failure-injection — `reclaim_expired_leases`가 예외를 던지면
    (DB 장애 시뮬레이션) `run_startup_recovery_gated`는 완료 표시 없이
    `RecoveryState`를 되돌려야 한다. 그 뒤로 어댑터 조회/이벤트 발행이
    전혀 일어나지 않아야 하고(체인이 중간에 끊겼으므로), 게이트는 계속
    DENY해야 한다(I-10 fail-closed)."""

    async def boom(_pool: asyncpg.Pool) -> int:
        raise RuntimeError("simulated lease-reclaim DB failure")

    monkeypatch.setattr(recovery_wiring, "reclaim_expired_leases", boom)

    async def resolve_adapter_should_not_be_called(user_id: uuid.UUID, exchange: str):
        raise AssertionError("lease reclaim이 실패했으면 어댑터를 조회하면 안 된다")

    async def publish_should_not_be_called(topic: str, payload: dict) -> None:
        raise AssertionError("lease reclaim이 실패했으면 이벤트를 발행하면 안 된다")

    state = await recovery_wiring.run_startup_recovery_gated(
        pool,
        resolve_adapter=resolve_adapter_should_not_be_called,
        publish=publish_should_not_be_called,
        enabled=True,
    )

    assert state.complete is False

    async def never_delegate(context: OrderContext) -> GateDecision:
        raise AssertionError("게이트가 위임되면 안 된다 — 복구 실패 후에도 거부 유지")

    gate = make_recovery_gate(state, never_delegate)
    decision = await gate(
        OrderContext(
            user_id=uuid.uuid4(), execution_id=1, exchange="bitget", mandate_revision_id=None
        )
    )
    assert decision.outcome == GateOutcome.DENY
    assert decision.reason_codes == (RECOVERY_IN_PROGRESS_REASON,)


# ---------------------------------------------------------------------------
# task-2794 DEEPEN — 다중 인스턴스(적대적 동시 재시작) 테스트
# ---------------------------------------------------------------------------


async def test_concurrent_restart_recovery_reclaim_is_race_safe(pool):
    """다중 인스턴스 — 두 프로세스가 동시에 재시작해 동시에
    `reclaim_expired_leases`를 호출해도 이중 계산이나 예외 없이 안전해야
    한다. 각 만료 lease 행은 정확히 한쪽 호출에서만 회수되어야 한다(둘 다
    보고 둘 다 지웠다고 셈하면 안 된다)."""
    user_id = await create_test_user(pool)
    exec_ids = [await create_execution(pool, user_id) for _ in range(20)]
    for eid in exec_ids:
        await _seed_lease(pool, eid, owner_id="dead-process", expired=True)

    reclaimed_a, reclaimed_b = await asyncio.gather(
        reclaim_expired_leases(pool), reclaim_expired_leases(pool)
    )

    assert reclaimed_a + reclaimed_b == len(exec_ids)
    for eid in exec_ids:
        assert await _lease_owner(pool, eid) is None


# ---------------------------------------------------------------------------
# task-2794 DEEPEN — 재전송(replay) 테스트
# ---------------------------------------------------------------------------


async def test_submit_order_replay_after_denial_succeeds_exactly_once(pool):
    """재전송(replay) — 복구 미완료로 거부된 주문을, 복구 완료 후 **같은
    Order 객체**로 재시도하면 정확히 한 번만 성공해야 한다. 거부된 첫
    시도가 부분 상태(예: 주문 행 일부 기록)를 남겨 재전송을 막거나
    중복 생성을 일으키면 안 된다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    order = _make_order(execution_id)
    state = RecoveryState()

    async def deny_delegate(context: OrderContext) -> GateDecision:
        raise AssertionError("게이트가 위임되면 안 된다 — 복구 미완료")

    with pytest.raises(OrderDeniedByRiskGateError):
        await submit_order(
            order,
            user_id=user_id,
            adapter=FakeExchangeAdapter(),
            pool=pool,
            pre_submit_gate=make_recovery_gate(state, deny_delegate),
        )

    state.mark_complete()

    async def allow_delegate(context: OrderContext) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
        )

    submitted = await submit_order(
        order,
        user_id=user_id,
        adapter=FakeExchangeAdapter(),
        pool=pool,
        pre_submit_gate=make_recovery_gate(state, allow_delegate),
    )

    assert submitted.client_order_id == order.client_order_id
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE client_order_id = $1", order.client_order_id
        )
    assert count == 1


# ---------------------------------------------------------------------------
# task-2794 DEEPEN — 수치 성능 단언
# ---------------------------------------------------------------------------


async def test_reclaim_expired_leases_wall_time_scales_sublinearly(pool):
    """수치 성능 단언 — `reclaim_expired_leases`는 단일 `DELETE` 왕복이다
    (per-row 왕복이 아니다). 행 수가 150배 늘어도 소요시간이 그만큼
    늘어나면 안 된다는 것을, 절대 ms 상수 대신 단일 행 대비 정규화한
    배율로 단언한다(공유 CI에서 절대 상수는 상시 적색을 낳으므로 —
    task-2773/2777/2778/2789와 동일 판단)."""
    user_id = await create_test_user(pool)

    single_exec = await create_execution(pool, user_id)
    await _seed_lease(pool, single_exec, owner_id="dead-single", expired=True)
    start = time.perf_counter()
    reclaimed_one = await reclaim_expired_leases(pool)
    single_seconds = time.perf_counter() - start
    assert reclaimed_one >= 1

    batch_size = 150
    batch_execs = [await create_execution(pool, user_id) for _ in range(batch_size)]
    for eid in batch_execs:
        await _seed_lease(pool, eid, owner_id="dead-batch", expired=True)
    start = time.perf_counter()
    reclaimed_many = await reclaim_expired_leases(pool)
    batch_seconds = time.perf_counter() - start
    assert reclaimed_many >= batch_size

    ratio = batch_seconds / max(single_seconds, 1e-6)
    budget_ratio = 20.0
    print(
        f"\nreclaim_expired_leases scaling: single={single_seconds * 1000:.3f}ms "
        f"x{batch_size}={batch_seconds * 1000:.3f}ms ratio={ratio:.1f} (budget={budget_ratio})"
    )
    assert ratio < budget_ratio, (
        f"reclaim_expired_leases가 {batch_size}배 행에서 단일 행 대비 {ratio:.1f}배로 "
        f"느려졌습니다(예산 {budget_ratio}배) — per-row 왕복으로 퇴화했을 가능성."
    )
