"""L4-28 §7.1 "outbox 지연(PENDING→SENDING) p99 ≤ 200 ms" — 환경 정규화
임계(print, 비차단) + DB 왕복 수 절대 단언(CI 게이트).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1(측정 지점
"outbox 타임스탬프"), §9 L4-28. 대상은 `OutboxDispatcher.dispatch_once()`
(L4-14) 단독 호출 — `outbox_dispatcher.py` 모듈이 이미 이 목표를 겨냥해
`DEFAULT_POLL_INTERVAL_SEC = 0.1  # §7.1 outbox 지연 p99 ≤ 200 ms`로
주석돼 있고, `OMS_OUTBOX_DISPATCH_DURATION_SECONDS`(L4-27, PLT-04)가 같은
구간(`_send_submit`)을 관측한다 — 이 파일은 그 관측 대상을 `limit=1`
호출의 벽시계 시간으로 재본다(클레임 tx 포함, `_send_submit` 자체보다
넓지만 "PENDING 행 하나가 완전히 처리되는 시간"이라는 관측 목적에는 더
가깝다).

**CI 게이트 = 순차 DB 왕복 수 정확 단언, 절대시간은 print(비차단)** —
`test_submit_internal_latency.py`/선례(task-1038/1521)와 동일 decision.

`dispatch_once(limit=1)` 1회(SUBMIT, 어댑터 ACK)의 왕복 수 구성(실측,
task-2323 `_discover_round_trips.py`로 확인):
  tx1 클레임(`claim_batch`, SKIP LOCKED) — BEGIN 1 + UPDATE...RETURNING 1
    + COMMIT 1 + 세션 리셋 1 = 4
  tx2 주문 잠금+SENT 전이 — BEGIN 1 + get_for_update(바깥 상태 확인) 1 +
    `orders.transition`(get_for_update 1 + set_config 1 + order_events
    INSERT 1 + conditional UPDATE 1 + audit_bridge.emit[4]) 8 + COMMIT 1 +
    리셋 1 = 12
  tx3 finalize(ACK) — BEGIN 1 + outbox `mark_done` fence UPDATE 1 +
    `orders.transition`(위와 동일 8) + COMMIT 1 + 리셋 1 = 12
  합계 = 4 + 12 + 12 = 28

negative test(I-10): `outbox_repo.claim_batch`(tx1당 정확히 1회 호출)가
왕복을 하나 더 내면 계수가 예산과 정확히 1 어긋난다(디스패처는 `order_repo`/
`outbox_repo`를 생성자 인자로 받으므로 `submit_order`와 달리 직접 교체
가능하다 — `get_for_update`처럼 한 호출 안에서 여러 번 불리는 메서드를
훅하면 +1이 아니라 +N이 되어 "정확히 1 어긋남" 단언이 깨진다).

DEEPEN(task-2802) — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#2323)
근거 보강: (1) 정규화 목표(`normalized_target_ms`)를 이제 실제로 단언한다
(절대 목표 `_P99_TARGET_MS` 자체는 여전히 비차단 print). (2) 어댑터
`place_order`가 분류 불가한 예외(네트워크 유실류)로 실패하면
`classify_submit_failure`가 `UNKNOWN`으로 분류해(`dispatch_outcome.py`) 주문을
`UNKNOWN`(재조회 대상)으로, outbox 행을 `DONE`(§6 F3 "재전송 금지")으로
확정한다 — 응답 유실 시 안전한 쪽(중복 전송 금지)으로 fail-close한다는
불변식의 증명이다.
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pytest

from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from src.services.oms.ports.repository import OutboxRow
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.performance.oms.conftest import (
    attach_round_trip_logger,
    measure_baseline_round_trip_p95_ms,
)
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate, submit_payload

_SAMPLE_COUNT = 100
_P99_TARGET_MS = 200.0  # §7.1 운영 목표 — 비차단(print), task-1038/1521 decision
_ROUND_TRIP_MULTIPLIER = 9
_DISPATCH_ONE_ROUND_TRIPS = 28  # 모듈 docstring 구성표 — 정확 단언(==)


async def _enqueue_pending_submit(
    pool: asyncpg.Pool, order_repo: PostgresOrderRepository, outbox_repo: OutboxRepository
) -> UUID:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, order_id)
        await outbox_repo.enqueue(
            conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
            not_before=datetime.now(timezone.utc),
        )
    return order_id


class _ChattyOutboxRepo(OutboxRepository):
    """negative 전용(I-10) — 클레임 전에 불필요한 왕복을 하나 더 낸다
    (`claim_batch`는 `dispatch_once` 1회당 정확히 1번만 불린다)."""

    async def claim_batch(
        self, conn: asyncpg.Connection, *, worker_id: str, limit: int, lease_sec: int
    ) -> list[OutboxRow]:
        await conn.fetchval("SELECT 1")
        return await super().claim_batch(
            conn, worker_id=worker_id, limit=limit, lease_sec=lease_sec
        )


async def _count_dispatch_once_round_trips(
    pool: asyncpg.Pool, *, outbox_repo_cls: type[OutboxRepository] = OutboxRepository
) -> int:
    order_repo, outbox_repo = PostgresOrderRepository(), outbox_repo_cls()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="w-count",
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
async def test_outbox_dispatch_p99_measured_and_round_trips_exact(pool: asyncpg.Pool) -> None:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="w-latency",
    )
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)

    latencies_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        await _enqueue_pending_submit(pool, order_repo, outbox_repo)
        started = time.perf_counter()
        report = await dispatcher.dispatch_once(limit=1)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert report.claimed == 1 and report.acknowledged == 1

    normalized_target_ms = max(_P99_TARGET_MS, _ROUND_TRIP_MULTIPLIER * baseline_p95_ms)
    round_trips = await _count_dispatch_once_round_trips(pool)

    latencies_ms.sort()
    print(
        f"\noutbox dispatch_once(limit=1) latency (n={_SAMPLE_COUNT}): "
        f"p50={statistics.median(latencies_ms):.2f}ms "
        f"p95={latencies_ms[int(len(latencies_ms) * 0.95)]:.2f}ms "
        f"p99={latencies_ms[int(len(latencies_ms) * 0.99)]:.2f}ms "
        f"max={latencies_ms[-1]:.2f}ms "
        f"(target p99<{_P99_TARGET_MS}ms, 정규화 목표={normalized_target_ms:.2f}ms, "
        f"비차단 — task-1038/1521 decision); "
        f"sequential DB round trips={round_trips} (budget=={_DISPATCH_ONE_ROUND_TRIPS})"
    )
    assert round_trips == _DISPATCH_ONE_ROUND_TRIPS, (
        f"outbox dispatch_once 순차 DB 왕복 수({round_trips})가 예산"
        f"({_DISPATCH_ONE_ROUND_TRIPS})과 다릅니다 — 구조 변경입니다"
        "(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )
    # 절대시간(p99 ≤ 200ms)은 여전히 게이트가 아니다(모듈 docstring, esc-826/task-1038
    # decision) — p99/max는 n=100 표본에서 단일 꼬리 샘플이라 그 자체가 노이즈에
    # 취약해(GC/스케줄링 지터 1회로도 튐) 게이트로 쓰지 않는다. 대신 이 환경의 기준
    # 왕복비용에 정규화한 목표(DEEPEN task-2802)를 더 안정적인 p95에 건다.
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    assert p95_ms < normalized_target_ms, (
        f"outbox dispatch_once p95({p95_ms:.2f}ms)가 정규화 목표({normalized_target_ms:.2f}ms "
        f"= max({_P99_TARGET_MS}, {_ROUND_TRIP_MULTIPLIER}x 기준왕복 {baseline_p95_ms:.2f}ms))를 "
        "넘었습니다 — 이 환경의 DB 왕복비용 대비 상대적인 성능 회귀입니다."
    )


async def test_dispatch_round_trip_gate_detects_extra_query(pool: asyncpg.Pool) -> None:
    """negative(I-10): outbox_repo가 왕복을 하나 더 내면 계수가 예산과 1 어긋난다."""
    round_trips = await _count_dispatch_once_round_trips(pool, outbox_repo_cls=_ChattyOutboxRepo)
    assert round_trips == _DISPATCH_ONE_ROUND_TRIPS + 1
    assert round_trips != _DISPATCH_ONE_ROUND_TRIPS


async def test_dispatch_classifies_adapter_failure_as_unknown_and_marks_outbox_done(
    pool: asyncpg.Pool,
) -> None:
    """failure-injection(DEEPEN task-2802): 어댑터 `place_order`가 분류 불가한
    예외(네트워크 유실류)로 실패하면 `dispatch_once`는 예외를 삼키고(전체
    루프를 죽이지 않음) 주문을 `UNKNOWN`, outbox 행을 `DONE`(재전송 금지)으로
    확정한다 — 응답 유실을 "성공"도 "안전한 재시도"도 아닌 별도 상태로 fail-close
    한다는 §6 F3의 증명이다."""
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()

    async def _raise_on_place(order: object) -> object:
        raise ConnectionError("simulated exchange network failure")

    adapter = ScriptedAdapter(on_place=_raise_on_place)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="w-failure",
    )
    order_id = await _enqueue_pending_submit(pool, order_repo, outbox_repo)

    report = await dispatcher.dispatch_once(limit=1)

    assert report.claimed == 1
    assert report.acknowledged == 0
    assert report.unknown == 1
    async with pool.acquire() as conn:
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", order_id
        )
        outbox_state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE order_id = $1", order_id
        )
    assert order_status == "UNKNOWN"
    assert outbox_state == "DONE"
