"""L4-28 §7.1 처리량 목표("outbox 100 cmd/s(단일 워커), inbox 500 ev/s") —
환경 정규화 임계(print, 비차단) + DB 왕복 수 절대 단언(CI 게이트).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1 처리량 행
("실행 200개 동시 tick, outbox 100 cmd/s(단일 워커), inbox 500 ev/s"), §9
L4-28. "실행 200개 동시 tick"은 execution/tick 엔진(이 리프의 files 범위
밖 — `L4_execution_...` 스펙이 아니라 실행 틱 스펙 소유) 항목이라 여기서는
다루지 않는다 — 이 파일은 OMS가 소유한 나머지 둘(outbox/inbox)만 잰다.

절대 처리량(cmd/s·ev/s)은 공유 CI 환경의 CPU/DB 편차에 좌우되는 신호라
게이트로 쓰지 않는다(esc-826/task-1038/1521과 동일 decision) — print로만
남긴다. 대신 배치 크기 N에 대해 "총 DB 왕복 수 == 고정 오버헤드 +
N * 행당 왕복 수"가 정확히 성립하는지를 단언한다 — 이는 `test_outbox_
dispatch_latency.py`/`test_inbox_lag.py`가 이미 증명한 행당 왕복 예산이
배치 처리에서도 그대로 선형 성립함(중복 조회·N+1 등 배치 특유의 회귀가
없음)을 보이는 구조적 회귀 가드다.

outbox: `dispatch_once(limit=N)`의 `claim_batch`는 N행을 **한 쿼리**로
한꺼번에 클레임한다(§5.1 SKIP LOCKED 배치) — 고정 오버헤드는 그 클레임
tx(BEGIN+UPDATE...RETURNING+COMMIT+리셋=4)뿐이고, 그 뒤 N개 행은 각자
자기 tx2+tx3(`test_outbox_dispatch_latency.py`가 측정한 24)을 순차로 쓴다.
총 왕복 = 4 + N*24.

inbox: `process_once(limit=N)`은 행마다 별도로 `claim_unprocessed`(FOR
UPDATE SKIP LOCKED 단건)를 부르므로(모듈 docstring "배치를 한 트랜잭션에
몰아넣지 않는다") 고정 오버헤드가 없다 — 총 왕복 = N * 20(부분체결 1행당
실측치, `ingest()`의 21에서 `insert_if_absent`+행 id 재조회 2회분을
`claim_unprocessed` 단건 조회 1회로 줄인 차이).

DEEPEN(task-2802) — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#2323)
근거 보강: (1) 절대 처리량(cmd/s·ev/s)은 여전히 비차단 print지만, 이 환경의
기준 왕복비용(`measure_baseline_round_trip_p95_ms`)에 정규화한 배치 소요
시간 상한(왕복 수 * 기준 왕복비용 * 안전배수)을 이제 실제로 단언한다 — 배치가
검증된 왕복 예산을 지키는 한, 실측 소요 시간도 그 왕복 수에 비례한 선형
범위 안에 있어야 한다는 회귀 가드다. (2) outbox 배치 안의 한 행이 어댑터
네트워크 실패로 실패해도(failure-injection) 배치 전체가 죽지 않고 나머지
행은 정상 처리된다는 §5.1 행별 try/except 격리(`OutboxDispatcher.
dispatch_once`)의 증명이다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pytest

from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.inbox_repository import InboxRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.performance.oms._fixtures import insert_open_order, partial_fill_event
from tests.performance.oms.conftest import (
    attach_round_trip_logger,
    measure_baseline_round_trip_p95_ms,
)
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate, submit_payload

_OUTBOX_TARGET_CMD_PER_SEC = 100.0  # §7.1 운영 목표 — 비차단(print)
_OUTBOX_BATCH_SIZE = 100
_OUTBOX_FIXED_ROUND_TRIPS = 4  # claim_batch tx(BEGIN+UPDATE+COMMIT+리셋)
_OUTBOX_PER_ROW_ROUND_TRIPS = 24  # test_outbox_dispatch_latency.py 실측(28-4)

_INBOX_TARGET_EV_PER_SEC = 500.0  # §7.1 운영 목표 — 비차단(print)
_INBOX_BATCH_SIZE = 500
_INBOX_PER_ROW_ROUND_TRIPS = 20  # process_once 부분체결 1행당 실측

# DEEPEN(task-2802) — 배치 총 소요시간의 정규화 상한(왕복 수 * 기준 왕복비용 *
# 안전배수). 실 왕복은 바닥값 `SELECT 1`보다 페이로드/도메인 로직이 더 들어가므로
# 안전배수를 둔다 — 절대 ms 상수가 아니라 이 환경의 실측 기준값에 비례한다.
_BATCH_ELAPSED_SAFETY_MULTIPLIER = 5


async def _seed_outbox_batch(pool: asyncpg.Pool, n: int) -> None:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    for _ in range(n):
        user_id = await create_test_user(pool)
        async with pool.acquire() as conn:
            order_id = await insert_order(conn, user_id, status="VALIDATED")
            view = await order_repo.get_for_update(conn, order_id)
            await outbox_repo.enqueue(
                conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
                not_before=datetime.now(timezone.utc),
            )


@pytest.mark.perf
async def test_outbox_single_worker_throughput_measured_and_round_trips_exact(
    pool: asyncpg.Pool,
) -> None:
    await _seed_outbox_batch(pool, _OUTBOX_BATCH_SIZE)
    adapter = ScriptedAdapter()

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=OutboxRepository(), order_repo=PostgresOrderRepository(),
        resolve_adapter=resolve, pre_send_gate=allow_gate, worker_id="w-throughput",
    )
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)
    queries = await attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    report = await dispatcher.dispatch_once(limit=_OUTBOX_BATCH_SIZE)
    elapsed_sec = time.perf_counter() - started

    achieved_cmd_per_sec = _OUTBOX_BATCH_SIZE / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = (
        _OUTBOX_FIXED_ROUND_TRIPS + _OUTBOX_BATCH_SIZE * _OUTBOX_PER_ROW_ROUND_TRIPS
    )
    elapsed_budget_sec = (
        expected_round_trips * (baseline_p95_ms / 1000.0) * _BATCH_ELAPSED_SAFETY_MULTIPLIER
    )
    print(
        f"\noutbox single-worker throughput: {report.claimed} claimed / "
        f"{report.acknowledged} acknowledged in {elapsed_sec:.3f}s = "
        f"{achieved_cmd_per_sec:.1f} cmd/s (target {_OUTBOX_TARGET_CMD_PER_SEC} cmd/s, 비차단 — "
        "task-1038/1521 decision); "
        f"sequential DB round trips={len(queries)} (budget=={expected_round_trips}); "
        f"elapsed budget={elapsed_budget_sec:.3f}s (baseline p95={baseline_p95_ms:.3f}ms)"
    )
    assert report.claimed == _OUTBOX_BATCH_SIZE
    assert report.acknowledged == _OUTBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"outbox 배치 처리 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(모듈 docstring 공식을 갱신하고 리뷰를 받으세요)."
    )
    assert elapsed_sec < elapsed_budget_sec, (
        f"outbox 배치({_OUTBOX_BATCH_SIZE}행) 처리 소요시간({elapsed_sec:.3f}s)이 정규화 상한"
        f"({elapsed_budget_sec:.3f}s)을 넘었습니다 — 이 환경의 DB 왕복비용 대비 상대적인 성능 회귀."
    )
    # 절대 처리량(cmd/s)은 게이트로 쓰지 않는다(모듈 docstring, esc-826/task-1038 decision).


async def _seed_inbox_backlog(pool: asyncpg.Pool, n: int) -> None:
    inbox_repo = InboxRepository()
    for _ in range(n):
        user_id = await create_test_user(pool)
        _, cid, exoid = await insert_open_order(pool, user_id)
        ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
        async with pool.acquire() as conn:
            inserted = await inbox_repo.insert_if_absent(conn, ev)
        assert inserted is True


@pytest.mark.perf
async def test_inbox_backlog_drain_throughput_measured_and_round_trips_exact(
    pool: asyncpg.Pool,
) -> None:
    await _seed_inbox_backlog(pool, _INBOX_BATCH_SIZE)
    processor = InboxProcessor(pool)
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)
    queries = await attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    processed = await processor.process_once(limit=_INBOX_BATCH_SIZE)
    elapsed_sec = time.perf_counter() - started

    achieved_ev_per_sec = processed / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = _INBOX_BATCH_SIZE * _INBOX_PER_ROW_ROUND_TRIPS
    elapsed_budget_sec = (
        expected_round_trips * (baseline_p95_ms / 1000.0) * _BATCH_ELAPSED_SAFETY_MULTIPLIER
    )
    print(
        f"\ninbox backlog drain throughput: {processed} processed in {elapsed_sec:.3f}s = "
        f"{achieved_ev_per_sec:.1f} ev/s (target {_INBOX_TARGET_EV_PER_SEC} ev/s, 비차단 — "
        "task-1038/1521 decision); "
        f"sequential DB round trips={len(queries)} (budget=={expected_round_trips}); "
        f"elapsed budget={elapsed_budget_sec:.3f}s (baseline p95={baseline_p95_ms:.3f}ms)"
    )
    assert processed == _INBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"inbox 백로그 드레인 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(모듈 docstring 공식을 갱신하고 리뷰를 받으세요)."
    )
    assert elapsed_sec < elapsed_budget_sec, (
        f"inbox 백로그({_INBOX_BATCH_SIZE}행) 드레인 소요시간({elapsed_sec:.3f}s)이 정규화 상한"
        f"({elapsed_budget_sec:.3f}s)을 넘었습니다 — 이 환경의 DB 왕복비용 대비 상대적인 성능 회귀."
    )
    # 절대 처리량(ev/s)은 게이트로 쓰지 않는다(모듈 docstring, esc-826/task-1038 decision).


_BATCH_WITH_FAILURE_SIZE = 10


async def test_outbox_batch_dispatch_survives_one_row_adapter_failure(pool: asyncpg.Pool) -> None:
    """failure-injection(DEEPEN task-2802): 배치(`dispatch_once(limit=N)`)
    안의 한 행이 어댑터 네트워크 실패로 실패해도(`place_order`가 예외를
    던짐) 나머지 행은 정상 처리된다 — `dispatch_once`의 행별 try/except
    격리(§5.1) 덕에 한 행의 실패가 배치 전체를 막지 않는다는 증명이다."""
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    await _seed_outbox_batch(pool, _BATCH_WITH_FAILURE_SIZE - 1)

    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        failing_order_id = await insert_order(conn, user_id, status="VALIDATED")
        view = await order_repo.get_for_update(conn, failing_order_id)
        await outbox_repo.enqueue(
            conn, order_id=failing_order_id, command_type="SUBMIT",
            payload=submit_payload(view), not_before=datetime.now(timezone.utc),
        )

    async def _flaky_place_order(order: Order) -> Order:
        if order.order_id == failing_order_id:
            raise ConnectionError("simulated exchange network failure mid-batch")
        return order.model_copy(
            update={"exchange_order_id": f"ex-{order.order_id}", "status": OrderStatus.SUBMITTED}
        )

    adapter = ScriptedAdapter(on_place=_flaky_place_order)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=outbox_repo, order_repo=order_repo, resolve_adapter=resolve,
        pre_send_gate=allow_gate, worker_id="w-batch-failure",
    )

    report = await dispatcher.dispatch_once(limit=_BATCH_WITH_FAILURE_SIZE)

    assert report.claimed == _BATCH_WITH_FAILURE_SIZE
    assert report.acknowledged == _BATCH_WITH_FAILURE_SIZE - 1
    assert report.unknown == 1
    async with pool.acquire() as conn:
        failing_status = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", failing_order_id
        )
    assert failing_status == "UNKNOWN"
