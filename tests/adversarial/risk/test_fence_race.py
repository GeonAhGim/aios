"""R-37 적대적 — kill switch(fence++) vs 동시 제출 경합, `orders` 트리거 거부.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-37 DoD("gather 경합
post-fence 0, 트리거 거부"), §3.6, §4.1 I1/I4, §10 cutover 행, 105번 동시성.

두 층의 경합 증명:
1. 단계형 gather(배리어) — activate가 "앞 그룹 F2 뒤·뒤 그룹 F1 앞"에
   커밋되도록 고정해 **post-fence 0**을 결정론적으로 실증한다.
2. 무단계 gather — 어떤 인터리빙에서도 성립하는 불변조건: fence가 움직인
   뒤 거래소에 닿은 부작용은 전부 계수·취소·감사된다(F1~place 사이 창은
   설계상 막을 수 없고 F2로 잡는다, §6).
트리거는 트랜잭션 안에서 cutover를 무장한 뒤 롤백해 검증한다(공유 테스트
DB의 다른 테스트에 영향 없음).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.observability.metric_names import SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL
from src.core.risk.decision import RiskDecision, RiskOutcome
from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.order_service.fenced_submit import (
    AUDIT_FENCE_STALE_PREVENTED,
    FenceStaleError,
    stale_pairs,
    submit_with_fence,
)
from src.services.order_service.gate import GateDecision, GateOutcome
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

_N_EARLY = 3
_N_LATE = 3
_N_UNSTAGED = 8


@dataclass(frozen=True)
class _Ctx:
    user_id: UUID
    execution_id: int
    decision: RiskDecision
    f0: Mapping[str, int]
    read: object  # FenceReader

    def gate(self) -> GateDecision:
        return GateDecision(
            outcome=GateOutcome.ALLOW, fence_snapshot=self.f0, decision_id=self.decision.decision_id
        )


@pytest.fixture
async def ctx(pool: asyncpg.Pool) -> _Ctx:
    user_id = await create_test_user(pool)
    execution_id = await seed_execution(pool, user_id)
    decision = await insert_decision(pool, user_id, execution_ref=f"exec:{execution_id}")
    read = fence_reader(pool, user_id, execution_id)
    return _Ctx(user_id, execution_id, decision, await read(), read)


async def _activate(pool: asyncpg.Pool, ctx: _Ctx) -> None:
    await activate_safety_control(
        PostgresRiskGateRepository(pool),
        tenant_id=ctx.user_id,
        actor_subject_id=ctx.user_id,
        actor_is_admin=True,
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"exec:{ctx.execution_id}",
        reason="fence-race",
        trace_id=uuid4(),
    )


def _recording_hook(ctx: _Ctx, fence_at_place: dict[UUID, Mapping[str, int]]):
    async def hook(order: Order) -> Order:
        fence_at_place[order.order_id] = await ctx.read()  # 부작용 시점의 fence
        return order.model_copy(
            update={"exchange_order_id": f"ex-{order.order_id}", "status": OrderStatus.SUBMITTED}
        )

    return hook


async def test_staged_gather_kill_switch_vs_submits_post_fence_zero(pool, ctx):
    metrics = SpyMetrics()
    fence_at_place: dict[UUID, Mapping[str, int]] = {}
    adapter = RecordingAdapter(on_place_order=_recording_hook(ctx, fence_at_place))
    early_done, activated = asyncio.Event(), asyncio.Event()
    finished = 0

    async def early() -> Order:
        nonlocal finished
        try:
            return await submit_with_fence(
                pool, adapter, make_order(ctx.execution_id), user_id=ctx.user_id,
                gate_decision=ctx.gate(), read_fences=ctx.read, metrics=metrics,
            )
        finally:
            finished += 1
            if finished == _N_EARLY:
                early_done.set()

    async def activator() -> None:
        await early_done.wait()
        await _activate(pool, ctx)
        activated.set()

    async def late() -> Order:
        calls = 0

        async def gated_read() -> Mapping[str, int]:
            nonlocal calls
            calls += 1
            if calls == 1:  # F1은 fence 증가가 커밋된 뒤에 읽는다(F0는 그 전)
                await activated.wait()
            return await ctx.read()

        return await submit_with_fence(
            pool, adapter, make_order(ctx.execution_id), user_id=ctx.user_id,
            gate_decision=ctx.gate(), read_fences=gated_read, metrics=metrics,
        )

    results = await asyncio.gather(
        *(early() for _ in range(_N_EARLY)), activator(), *(late() for _ in range(_N_LATE)),
        return_exceptions=True,
    )
    early_results, late_results = results[:_N_EARLY], results[_N_EARLY + 1 :]

    assert all(isinstance(r, Order) and r.status == OrderStatus.SUBMITTED for r in early_results)
    assert all(isinstance(r, FenceStaleError) for r in late_results)
    assert adapter.place_order_call_count == _N_EARLY
    assert all(fence == ctx.f0 for fence in fence_at_place.values()), "post-fence 부작용 발생"
    assert metrics.counters.get(SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL, 0) == 0
    assert adapter.cancelled_exchange_order_ids == []
    for submitted in early_results:
        row = await order_row(pool, submitted.order_id)
        assert (row["status"], row["risk_decision_id"]) == ("SUBMITTED", ctx.decision.decision_id)
    for error in late_results:
        assert (await order_row(pool, error.order_id))["status"] == "FAILED"
        assert await audit_count(pool, AUDIT_FENCE_STALE_PREVENTED, error.order_id) == 1
        assert error.stale_pairs == (f"STRATEGY_DEPLOYMENT:exec:{ctx.execution_id}",)


async def test_unstaged_gather_every_post_fence_effect_is_detected_and_reversed(pool, ctx):
    metrics = SpyMetrics()
    fence_at_place: dict[UUID, Mapping[str, int]] = {}
    adapter = RecordingAdapter(on_place_order=_recording_hook(ctx, fence_at_place))

    async def submit() -> Order:
        return await submit_with_fence(
            pool, adapter, make_order(ctx.execution_id), user_id=ctx.user_id,
            gate_decision=ctx.gate(), read_fences=ctx.read, metrics=metrics,
        )

    results = await asyncio.gather(
        *(submit() for _ in range(_N_UNSTAGED)), _activate(pool, ctx), return_exceptions=True
    )
    submits = results[:_N_UNSTAGED]
    unexpected = [
        r for r in submits if isinstance(r, Exception) and not isinstance(r, FenceStaleError)
    ]
    assert unexpected == []

    leaked = {oid for oid, fence in fence_at_place.items() if stale_pairs(ctx.f0, fence)}
    stale_count = 0
    for result in submits:
        if isinstance(result, FenceStaleError):
            stale_count += 1
            assert result.order_id not in fence_at_place  # 거래소에 닿지 않았다
            assert (await order_row(pool, result.order_id))["status"] == "FAILED"
            continue
        row = await order_row(pool, result.order_id)
        assert row["risk_decision_id"] == ctx.decision.decision_id
        assert row["status"] in ("SUBMITTED", "CANCELLED")
        if result.order_id in leaked:  # fence 뒤 부작용은 반드시 되돌려졌다
            assert row["status"] == "CANCELLED"
            assert result.exchange_order_id in adapter.cancelled_exchange_order_ids
    detected = metrics.counters.get(SAFETY_POST_FENCE_SIDE_EFFECT_COUNT_TOTAL, 0)
    assert detected >= len(leaked)  # F2 검출은 보수적(상위집합)
    assert len(adapter.cancelled_exchange_order_ids) == detected
    assert adapter.place_order_call_count == _N_UNSTAGED - stale_count


async def _insert_raw(
    conn: asyncpg.Connection,
    ctx: _Ctx,
    *,
    risk_decision_id: UUID | None = None,
    is_liquidation: bool = False,
    liquidation_request_id: UUID | None = None,
    created_at=None,
) -> UUID:
    return await conn.fetchval(
        """
        INSERT INTO orders (
            user_id, client_order_id, strategy_id, strategy_version, execution_id, symbol,
            exchange, side, order_type, quantity, status, is_liquidation, asset_class,
            risk_decision_id, liquidation_request_id, created_at
        ) VALUES ($1, $2, 'fence-race', '1.0.0', $3, 'BTC/USDT', 'bitget', 'BUY', 'MARKET',
                  0.01, 'CREATED', $4, 'CRYPTO', $5, $6, COALESCE($7, now()))
        RETURNING order_id
        """,
        ctx.user_id, f"raw-{uuid4().hex}", ctx.execution_id, is_liquidation,
        risk_decision_id, liquidation_request_id, created_at,
    )


async def test_trigger_rejects_order_without_decision_once_cutover_armed(pool, ctx):
    arm_sql = (
        "UPDATE orders_risk_decision_cutover SET cutover_at = now(), armed_by = 'test' "
        "WHERE id = 1 AND cutover_at IS NULL RETURNING cutover_at"
    )
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await _insert_raw(conn, ctx)  # 비무장(현재 운영 상태): 결정 없는 INSERT 통과
            armed_at = await conn.fetchval(arm_sql)
            assert armed_at is not None
            assert await conn.fetchval(arm_sql) is None  # 단조: 재무장은 no-op

            with pytest.raises(asyncpg.CheckViolationError, match="has no risk_decision_id"):
                async with conn.transaction():
                    await _insert_raw(conn, ctx)
            with_decision = await _insert_raw(conn, ctx, risk_decision_id=ctx.decision.decision_id)
            await _insert_raw(conn, ctx, created_at=armed_at - timedelta(seconds=1))  # 이전 행
            with pytest.raises(asyncpg.CheckViolationError):  # UPDATE로 참조 제거 우회 불가
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET risk_decision_id = NULL WHERE order_id = $1",
                        with_decision,
                    )
        finally:
            await tr.rollback()


@pytest.mark.parametrize("case", ["other_tenant", "deny", "expired", "unknown"])
async def test_trigger_rejects_invalid_decision_reference_even_when_disarmed(pool, ctx, case):
    if case == "other_tenant":
        decision_id = (await insert_decision(pool, await create_test_user(pool))).decision_id
    elif case == "deny":
        deny = await insert_decision(pool, ctx.user_id, outcome=RiskOutcome.DENY)
        decision_id = deny.decision_id
    elif case == "expired":
        expired = await insert_decision(pool, ctx.user_id, ttl=timedelta(seconds=-1))
        decision_id = expired.decision_id
    else:
        decision_id = uuid4()
    async with pool.acquire() as conn:
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            await _insert_raw(conn, ctx, risk_decision_id=decision_id)
        assert await _insert_raw(conn, ctx, risk_decision_id=ctx.decision.decision_id)  # 대조군


async def test_trigger_liquidation_requires_request_id(pool, ctx):
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError, match="requires liquidation_request_id"):
            await _insert_raw(conn, ctx, is_liquidation=True)
        assert await _insert_raw(conn, ctx, is_liquidation=True, liquidation_request_id=uuid4())
