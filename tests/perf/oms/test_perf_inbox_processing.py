"""L4-28 DEEPEN(task-2804, DEPTH 감사 task-2722 #2329행) — §7.1 "inbox 처리
지연 p99(received_at→전이 commit) ≤ 300 ms"의 두 결손 보강: 실패 주입
테스트 + 게이팅 수치 단언.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1(측정 지점
"inbox"), §9 L4-28. 원 리프(task-2323, `tests/performance/oms/
test_inbox_lag.py`, commit `034fed00`)와 동일하게 `InboxProcessor.ingest()`
(L4-15)를 부분체결로 대상 삼되(원 리프 docstring 참조 — `position_ledger`
호출을 측정 범위 밖으로 격리하는 이유는 그대로다):

1. `test_inbox_ingest_latency_within_environment_normalized_bound` — 원 리프가
   계산만 하고 print했던 환경 정규화 목표를 **실제로 단언**한다.
2. `test_inbox_ingest_fails_closed_when_fills_repo_connection_drops` —
   `ingest()`의 단일 트랜잭션 안, `fills.insert_if_absent` 단계에서 DB 연결
   장애를 흉내낸다. 이 함수는 "inbox INSERT + 처리"를 한 트랜잭션(§5.1)으로
   묶는다 — 실패 시 inbox 행 자체(방금 넣은 `provider_event_inbox` 행
   포함)까지 통째로 롤백돼야 한다. 부분 커밋(inbox만 남고 fill 실패)은
   중복 전달 흡수 계약(F9, "같은 키는 한 번만 처리")을 깨므로 fail-closed
   증명이다.

왕복 수 예산(21)은 원 리프 산출과 동일 — task-2804 작업 중 재확인했다.
"""
from __future__ import annotations

import statistics
import time
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent
from tests.integration.oms.conftest import create_test_user
from tests.perf.oms.conftest import attach_round_trip_logger, measure_baseline_round_trip_p95_ms
from tests.performance.oms._fixtures import insert_open_order, partial_fill_event

_SAMPLE_COUNT = 100
_P99_TARGET_MS = 300.0  # §7.1 운영 목표
# 게이팅 배수 — p95 기준(test_perf_submit_internal.py와 동일 근거: p99는 표본
# 100개의 사실상 최댓값이라 단일 OS 스케줄링 튐에도 흔들린다).
_GATE_MULTIPLIER = 30
_INGEST_PARTIAL_ROUND_TRIPS = 21  # 원 리프 실측 구성표 그대로(task-2804 재확인)


class _FailingFillsRepo(FillsRepository):
    """실패 주입 전용 — `insert_if_absent` 단계에서 DB 연결 장애를 흉내낸다."""

    async def insert_if_absent(self, conn: asyncpg.Connection, fill: FillEvent) -> bool:  # type: ignore[override]
        raise ConnectionError("simulated DB connectivity failure during fill insert")


async def _count_ingest_round_trips(pool: asyncpg.Pool) -> int:
    user_id = await create_test_user(pool)
    processor = InboxProcessor(pool)
    queries = await attach_round_trip_logger(pool)

    _, cid, exoid = await insert_open_order(pool, user_id)  # 워밍업
    await processor.ingest(partial_fill_event(exchange_order_id=exoid, client_order_id=cid))

    _, cid, exoid = await insert_open_order(pool, user_id)
    queries.clear()
    ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
    result = await processor.ingest(ev)
    assert result is True
    return len(queries)


@pytest.mark.perf
async def test_inbox_ingest_latency_within_environment_normalized_bound(pool: asyncpg.Pool) -> None:
    user_id = await create_test_user(pool)
    processor = InboxProcessor(pool)
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)

    latencies_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        _, cid, exoid = await insert_open_order(pool, user_id)
        ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
        started = time.perf_counter()
        result = await processor.ingest(ev)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert result is True

    normalized_target_ms = max(_P99_TARGET_MS, _GATE_MULTIPLIER * baseline_p95_ms)
    latencies_ms.sort()
    p50_ms = statistics.median(latencies_ms)
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    p99_ms = latencies_ms[int(len(latencies_ms) * 0.99)]
    print(
        f"\ninbox ingest(partial fill) latency (n={_SAMPLE_COUNT}): "
        f"p50={p50_ms:.2f}ms p95={p95_ms:.2f}ms p99={p99_ms:.2f}ms max={latencies_ms[-1]:.2f}ms "
        f"(baseline p95={baseline_p95_ms:.2f}ms, 정규화 게이트={normalized_target_ms:.2f}ms)"
    )
    # p99/max는 정보용 print — p95를 게이트로 쓰는 이유는 모듈 상단 상수 주석 참조.
    assert p95_ms <= normalized_target_ms, (
        f"inbox ingest p95({p95_ms:.2f}ms)가 환경 정규화 목표({normalized_target_ms:.2f}ms)를 "
        "넘었습니다."
    )

    round_trips = await _count_ingest_round_trips(pool)
    assert round_trips == _INGEST_PARTIAL_ROUND_TRIPS, (
        f"inbox ingest 순차 DB 왕복 수({round_trips})가 예산({_INGEST_PARTIAL_ROUND_TRIPS})과 "
        "다릅니다 — 구조 변경입니다(리뷰를 받으세요)."
    )


async def test_inbox_ingest_fails_closed_when_fills_repo_connection_drops(
    pool: asyncpg.Pool,
) -> None:
    """실패 주입(DEPTH 감사 결손 보강) — `fills.insert_if_absent`가 예외를
    내면 `ingest()`의 단일 트랜잭션(inbox INSERT 포함)이 통째로 롤백돼야
    한다. 부분 커밋은 F9(중복 전달 흡수) 계약을 깬다: inbox 행만 남으면
    재전달된 같은 이벤트가 '이미 처리됨'으로 오인돼 영구히 무시된다."""
    user_id = await create_test_user(pool)
    order_id, cid, exoid = await insert_open_order(pool, user_id)
    processor = InboxProcessor(pool, fills_repo=_FailingFillsRepo())
    ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)

    with pytest.raises(ConnectionError):
        await processor.ingest(ev)

    async with pool.acquire() as conn:
        inbox_count = await conn.fetchval(
            "SELECT count(*) FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
            ev.venue,
            ev.provider_event_id,
        )
        order_row = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", order_id
        )
    assert inbox_count == 0, (
        "fills 삽입 실패 시 provider_event_inbox 행도 롤백돼야 합니다(F9 중복 흡수 계약)."
    )
    assert order_row["status"] == OrderStatus.SUBMITTED.value
    assert order_row["filled_quantity"] == Decimal("0")

    # negative 확인: 같은 이벤트를 정상 fills_repo로 재시도하면 이번엔 처리된다
    # (앞선 실패가 inbox 큐를 영구히 막지 않았음을 증명 — 부분 커밋이 아니었다).
    retry_processor = InboxProcessor(pool)
    retried = await retry_processor.ingest(
        partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
    )
    assert retried is True
