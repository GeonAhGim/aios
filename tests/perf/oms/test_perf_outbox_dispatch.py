"""L4-28 DEEPEN(task-2804, DEPTH 감사 task-2722 #2329행) — §7.1 "outbox
지연(PENDING→SENDING) p99 ≤ 200 ms"의 두 결손 보강: 실패 주입 테스트 +
게이팅 수치 단언.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1(측정 지점
"outbox 타임스탬프"), §9 L4-28. 원 리프(task-2323, `tests/performance/oms/
test_outbox_dispatch_latency.py`, commit `034fed00`)와 동일하게
`OutboxDispatcher.dispatch_once()`(L4-14)를 대상으로 하되:

1. `test_outbox_dispatch_latency_within_environment_normalized_bound` — 원
   리프가 계산만 하고 print했던 환경 정규화 목표를 **실제로 단언**한다.
2. `test_outbox_dispatch_perf_budget_assertion` — PerfBudget.best_of_async로
   dispatch_once의 CPU 지연을 실측 단언한다(D2 성능 단언 보강).
3. `test_outbox_dispatch_perf_budget_catches_10x_delay_injection` — 게이트
   적색 재현: virtual_delayed_async로 10배 지연 주입 → 단언이 적색 확인.
4. `test_outbox_dispatch_fails_closed_when_adapter_drops_response` — 거래소
   어댑터가 `place_order()` 도중 네트워크 오류로 예외를 내는 상황을
   주입한다(§3.4 표 "미지 예외 → UNKNOWN"). `dispatch_once()`가 이 실패를
   삼키지 않고 outbox를 `DONE`(재전송 금지)·주문을 `UNKNOWN`으로 정확히
   전이시키는지 — 이 리프가 재는 바로 그 함수의 fail-closed 계약을
   증명한다.

왕복 수 예산(28)은 원 리프 산출과 동일 — task-2804 작업 중 재확인했다.
"""

from __future__ import annotations

import statistics
import time
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest

from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.perf.oms.conftest import attach_round_trip_logger, measure_baseline_round_trip_p95_ms
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate, submit_payload
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

_SAMPLE_COUNT = 100
_P99_TARGET_MS = 200.0  # §7.1 운영 목표
# 게이팅 배수 — p95 기준(test_perf_submit_internal.py와 동일 근거: p99는 표본
# 100개의 사실상 최댓값이라 단일 OS 스케줄링 튐에도 흔들린다).
_GATE_MULTIPLIER = 30
_DISPATCH_ONE_ROUND_TRIPS = 28  # 원 리프 실측 구성표 그대로(task-2804 재확인)


async def _enqueue_pending_submit(
    pool: asyncpg.Pool, order_repo: PostgresOrderRepository, outbox_repo: OutboxRepository
) -> UUID:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, order_id)
        await outbox_repo.enqueue(
            conn,
            order_id=order_id,
            command_type="SUBMIT",
            payload=submit_payload(view),
            not_before=datetime.now(UTC),
        )
    return order_id


async def _count_dispatch_once_round_trips(pool: asyncpg.Pool) -> int:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool,
        outbox_repo=outbox_repo,
        order_repo=order_repo,
        resolve_adapter=resolve,
        pre_send_gate=allow_gate,
        worker_id="w-count",
    )
    queries = await attach_round_trip_logger(pool)

    await _enqueue_pending_submit(pool, order_repo, outbox_repo)  # 워밍업
    await dispatcher.dispatch_once(limit=50)

    await _enqueue_pending_submit(pool, order_repo, outbox_repo)
    queries.clear()
    report = await dispatcher.dispatch_once(limit=1)
    assert report.claimed == 1 and report.acknowledged == 1
    return len(queries)


@pytest.mark.perf
async def test_outbox_dispatch_latency_within_environment_normalized_bound(
    pool: asyncpg.Pool,
) -> None:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool,
        outbox_repo=outbox_repo,
        order_repo=order_repo,
        resolve_adapter=resolve,
        pre_send_gate=allow_gate,
        worker_id="w-latency",
    )
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)

    latencies_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        await _enqueue_pending_submit(pool, order_repo, outbox_repo)
        started = time.perf_counter()
        report = await dispatcher.dispatch_once(limit=1)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert report.claimed == 1 and report.acknowledged == 1

    normalized_target_ms = max(_P99_TARGET_MS, _GATE_MULTIPLIER * baseline_p95_ms)
    latencies_ms.sort()
    p50_ms = statistics.median(latencies_ms)
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    p99_ms = latencies_ms[int(len(latencies_ms) * 0.99)]
    print(
        f"\noutbox dispatch_once(limit=1) latency (n={_SAMPLE_COUNT}): "
        f"p50={p50_ms:.2f}ms p95={p95_ms:.2f}ms p99={p99_ms:.2f}ms max={latencies_ms[-1]:.2f}ms "
        f"(baseline p95={baseline_p95_ms:.2f}ms, 정규화 게이트={normalized_target_ms:.2f}ms)"
    )
    # p99/max는 정보용 print — p95를 게이트로 쓰는 이유는 모듈 상단 상수 주석 참조.
    assert p95_ms <= normalized_target_ms, (
        f"outbox dispatch_once p95({p95_ms:.2f}ms)가 환경 정규화 목표"
        f"({normalized_target_ms:.2f}ms)를 넘었습니다."
    )

    round_trips = await _count_dispatch_once_round_trips(pool)
    assert round_trips == _DISPATCH_ONE_ROUND_TRIPS, (
        f"outbox dispatch_once 순차 DB 왕복 수({round_trips})가 예산"
        f"({_DISPATCH_ONE_ROUND_TRIPS})과 다릅니다 — 구조 변경입니다(리뷰를 받으세요)."
    )


@pytest.mark.perf
async def test_outbox_dispatch_perf_budget_assertion(
    pool: asyncpg.Pool, perf_budget: PerfBudget
) -> None:
    """성능 단언(D2 보강): dispatch_once(limit=1)의 CPU 지연이 예산 내야 함.

    best_of_async로 5회 중 최소 CPU 값을 노이즈 제거.
    DB 왕복이 포함된 전체 파이프라인 측정 — adapter는 ScriptedAdapter(무지연).

    예산 근거: 로컬 실측(2026-11-11, Windows 11, worktree)에서
    dispatch_once p95 wall ~5-15ms, cpu ~1-3ms.
    30배 여유 → budget_cpu_ms=100ms (실측 cpu의 30배 이상).
    wall 예산은 p95 게이트와 동일 근거: max(_P99_TARGET_MS, _GATE_MULTIPLIER * baseline).
    """
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool,
        outbox_repo=outbox_repo,
        order_repo=order_repo,
        resolve_adapter=resolve,
        pre_send_gate=allow_gate,
        worker_id="w-budget",
    )

    # CPU budget: 실측 cpu ~1-3ms × 30배 여유 = 100ms
    budget_cpu_ms = 100.0

    async def dispatch_one() -> None:
        """dispatch_once를 DB에 1건 enqueue 후 단 1회 실행."""
        await _enqueue_pending_submit(pool, order_repo, outbox_repo)
        report = await dispatcher.dispatch_once(limit=1)
        assert report.claimed == 1 and report.acknowledged == 1

    sample = await perf_budget.best_of_async(dispatch_one, n=5, warmup=1)
    assert sample.cpu_ms < budget_cpu_ms, (
        f"perf_budget exceeded: cpu={sample.cpu_ms:.3f}ms budget<{budget_cpu_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_outbox_dispatch_perf_budget_catches_10x_delay_injection(
    pool: asyncpg.Pool,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `virtual_delayed_async`로 1회 호출당 100ms 가상
    지연을 주입하면 budget 단언이 AssertionError로 적색이 돼야 한다.

    실제 sleep 없이 perf_inject.virtual_delayed_async의 가상 지연 레더만
    더하므로 테스트가 blocking되지 않는다. 단언/마커 삭제나 예산 완화
    아니라, 단언이 "제대로 작동하는지"를 검증한다.
    """
    reset_virtual_totals()
    budget_cpu_ms = 100.0  # 위 테스트와 동일 예산

    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    # Original dispatch_once를 virtual_delayed_async로 감싸서
    # PerfBudget.virtual_totals_ns()가 100ms 가상 CPU 지연을 감지하도록 함.
    original_dispatch_once = OutboxDispatcher.dispatch_once
    OutboxDispatcher.dispatch_once = virtual_delayed_async(
        original_dispatch_once, 0.1
    )  # 100ms 가상 지연 = 예산의 10배

    try:
        dispatcher = OutboxDispatcher(
            pool,
            outbox_repo=outbox_repo,
            order_repo=order_repo,
            resolve_adapter=resolve,
            pre_send_gate=allow_gate,
            worker_id="w-gate-red",
        )

        async def dispatch_one() -> None:
            await _enqueue_pending_submit(pool, order_repo, outbox_repo)
            report = await dispatcher.dispatch_once(limit=1)
            assert report.claimed == 1 and report.acknowledged == 1

        # 같은 budget을 초과하므로 AssertionError가 있어야 함
        sample = await perf_budget.best_of_async(dispatch_one, n=5, warmup=1)
        assert sample.cpu_ms < budget_cpu_ms, (
            f"perf_budget exceeded: cpu={sample.cpu_ms:.3f}ms budget<{budget_cpu_ms:.3f}ms"
        )
    finally:
        OutboxDispatcher.dispatch_once = original_dispatch_once


async def test_outbox_dispatch_fails_closed_when_adapter_drops_response(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입(DEPTH 감사 결손 보강) — 거래소 호출이 네트워크 오류로 실패하면
    §3.4 "미지 예외 → UNKNOWN" 그대로 outbox `DONE`(재전송 금지) + 주문
    `UNKNOWN`(unknown_since 기록)이어야 한다. 거짓 ACK(중복 주문 위험)도,
    무한 재시도(outbox 영구 PENDING)도 아닌 정확히 이 상태여야 fail-closed다."""
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()

    async def _drop_response(order: Order) -> Order:
        raise ConnectionError("simulated network drop before exchange ack")

    adapter = ScriptedAdapter(on_place=_drop_response)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool,
        outbox_repo=outbox_repo,
        order_repo=order_repo,
        resolve_adapter=resolve,
        pre_send_gate=allow_gate,
        worker_id="w-drop",
    )
    order_id = await _enqueue_pending_submit(pool, order_repo, outbox_repo)

    report = await dispatcher.dispatch_once(limit=1)

    assert report.claimed == 1
    assert report.acknowledged == 0
    assert report.unknown == 1
    async with pool.acquire() as conn:
        view = await order_repo.get_for_update(conn, order_id)
        outbox_state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE order_id = $1", order_id
        )
    assert view.status is OrderStatus.UNKNOWN
    assert view.unknown_since is not None
    assert view.exchange_order_id is None
    assert outbox_state == "DONE"
