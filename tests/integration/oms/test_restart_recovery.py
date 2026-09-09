"""task-2151(L4-18a) 통합테스트 — 만료 execution lease 회수 + 복구 완료
전 submit_order 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6(이 배치는 ①
만료 lease 회수와 ⑤ 복구 완료 전 submit_order 거부만 다룬다 — ②③④는
task-2310), §9 L4-18. DoD: 만료 lease 1건이 심어진 상태로 복구를 돌리면
그 lease가 회수되고, 복구 완료 플래그가 서기 전 submit_order 호출이
구체 에러코드(`OMS_RECOVERY_IN_PROGRESS`)로 거부된다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
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
