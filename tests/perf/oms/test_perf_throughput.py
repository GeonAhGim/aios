"""L4-28 DEEPEN(task-2804, DEPTH 감사 task-2722 #2329행) — §7.1 처리량 목표
("outbox 100 cmd/s(단일 워커), inbox 500 ev/s")의 두 결손 보강: 실패 주입
테스트 + 게이팅 수치 단언.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1 처리량 행, §9
L4-28. 원 리프(task-2323, `tests/performance/oms/test_oms_throughput.py`,
commit `034fed00`)와 동일하게 "총 DB 왕복 수 == 고정 오버헤드 + N * 행당
왕복 수"의 선형 회귀 가드는 그대로 두고 다음을 더한다:

1. 두 처리량 테스트에 **게이팅** 시간 상한을 추가한다 — 절대 cmd/s·ev/s
   목표는 여전히 print 비차단(공유 CI 편차 신호, 원 리프 decision)이지만,
   "이미 정확히 안 예산으로 확정된 왕복 수 × 이 환경의 실측 기준 왕복비용"에
   상대적인 총 소요시간 상한은 이 환경 자체의 기준선일 뿐이므로 안전하게
   게이트로 쓸 수 있다(원 리프는 이 정규화조차 하지 않고 절대치만 print했다).
2. `test_outbox_batch_isolates_one_adapter_failure` /
   `test_inbox_backlog_isolates_one_poisoned_event` — 배치 중간에 정확히
   하나의 명령/이벤트만 실패하도록 주입해, 나머지가 격리되어 처리되는지
   증명한다(전체 배치가 한 건의 장애로 멈추면 처리량 목표 자체가 무의미해
   진다 — 이 리프가 처음으로 배치 처리량 경로에 실패 주입을 더한다).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.inbox_repository import InboxRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from src.services.oms.contracts.v1_events import FillEvent
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.perf.oms.conftest import attach_round_trip_logger, measure_baseline_round_trip_p95_ms
from tests.performance.oms._fixtures import insert_open_order, partial_fill_event
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate, submit_payload

_OUTBOX_TARGET_CMD_PER_SEC = 100.0  # §7.1 운영 목표 — 비차단(print)
_OUTBOX_BATCH_SIZE = 100
_OUTBOX_FIXED_ROUND_TRIPS = 4  # claim_batch tx(BEGIN+UPDATE+COMMIT+리셋)
_OUTBOX_PER_ROW_ROUND_TRIPS = 24  # test_perf_outbox_dispatch.py 실측(28-4)

_INBOX_TARGET_EV_PER_SEC = 500.0  # §7.1 운영 목표 — 비차단(print)
_INBOX_BATCH_SIZE = 500
_INBOX_PER_ROW_ROUND_TRIPS = 20  # process_once 부분체결 1행당 실측

# 배치 총 소요시간의 환경 정규화 상한 = (왕복 수 예산 × 기준 왕복 p95) × 이
# 배수. 순차 단일 커넥션이라 "왕복 수 × 기준 비용"이 자연스러운 하한이고,
# CPU 스케줄링·GC 등 비-DB 오버헤드를 흡수하도록 넉넉히 잡는다(원 리프의
# 단발 호출 배수 9보다 낮춘 이유: N회 반복 평균이라 개별 튐이 상쇄된다).
_THROUGHPUT_TIME_MULTIPLIER = 5.0


async def _seed_outbox_batch(pool: asyncpg.Pool, n: int) -> list[UUID]:
    order_repo, outbox_repo = PostgresOrderRepository(), OutboxRepository()
    order_ids: list[UUID] = []
    for _ in range(n):
        user_id = await create_test_user(pool)
        async with pool.acquire() as conn:
            order_id = await insert_order(conn, user_id, status="VALIDATED")
            view = await order_repo.get_for_update(conn, order_id)
            await outbox_repo.enqueue(
                conn, order_id=order_id, command_type="SUBMIT", payload=submit_payload(view),
                not_before=datetime.now(timezone.utc),
            )
        order_ids.append(order_id)
    return order_ids


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
    normalized_max_elapsed_sec = (
        expected_round_trips * baseline_p95_ms / 1000.0
    ) * _THROUGHPUT_TIME_MULTIPLIER
    print(
        f"\noutbox single-worker throughput: {report.claimed} claimed / "
        f"{report.acknowledged} acknowledged in {elapsed_sec:.3f}s = "
        f"{achieved_cmd_per_sec:.1f} cmd/s (target {_OUTBOX_TARGET_CMD_PER_SEC} cmd/s, 비차단); "
        f"round trips={len(queries)} (budget=={expected_round_trips}), "
        f"정규화 게이트={normalized_max_elapsed_sec:.3f}s"
    )
    assert report.claimed == _OUTBOX_BATCH_SIZE
    assert report.acknowledged == _OUTBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"outbox 배치 처리 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(리뷰를 받으세요)."
    )
    assert elapsed_sec <= normalized_max_elapsed_sec, (
        f"outbox 배치({_OUTBOX_BATCH_SIZE}건) 소요시간({elapsed_sec:.3f}s)이 환경 정규화 상한"
        f"({normalized_max_elapsed_sec:.3f}s)을 넘었습니다."
    )


async def test_outbox_batch_isolates_one_adapter_failure(pool: asyncpg.Pool) -> None:
    """실패 주입(DEPTH 감사 결손 보강) — 배치 중 정확히 한 건의 어댑터 호출이
    네트워크 오류로 실패해도 나머지 (N-1)건은 정상 처리되고, 왕복 수 예산도
    그대로 성립해야 한다(UNKNOWN·ACK 두 경로 모두 `done`+`transition` 구조가
    같아 왕복 수가 동일함을 이용 — `test_perf_outbox_dispatch.py`의 실패
    주입 테스트가 이미 증명한 사실을 배치 규모에서 재확인한다)."""
    n = 20
    await _seed_outbox_batch(pool, n)
    call_count = {"n": 0}
    failing_call_index = 10

    async def _flaky_place(order: Order) -> Order:
        call_count["n"] += 1
        if call_count["n"] == failing_call_index:
            raise ConnectionError("simulated network drop mid-batch")
        return order.model_copy(
            update={"exchange_order_id": f"ex-{uuid4()}", "status": OrderStatus.ACKNOWLEDGED}
        )

    adapter = ScriptedAdapter(on_place=_flaky_place)

    async def resolve(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
        return adapter

    dispatcher = OutboxDispatcher(
        pool, outbox_repo=OutboxRepository(), order_repo=PostgresOrderRepository(),
        resolve_adapter=resolve, pre_send_gate=allow_gate, worker_id="w-flaky",
    )
    queries = await attach_round_trip_logger(pool)
    queries.clear()

    report = await dispatcher.dispatch_once(limit=n)

    expected_round_trips = _OUTBOX_FIXED_ROUND_TRIPS + n * _OUTBOX_PER_ROW_ROUND_TRIPS
    assert report.claimed == n
    assert report.acknowledged == n - 1
    assert report.unknown == 1
    assert len(queries) == expected_round_trips, (
        "실패 주입된 배치의 순차 DB 왕복 수가 예산과 다릅니다 — UNKNOWN 경로가 ACK와 "
        "다른 쓰기 구조를 탄다는 뜻이므로 회귀입니다."
    )


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
    normalized_max_elapsed_sec = (
        expected_round_trips * baseline_p95_ms / 1000.0
    ) * _THROUGHPUT_TIME_MULTIPLIER
    print(
        f"\ninbox backlog drain throughput: {processed} processed in {elapsed_sec:.3f}s = "
        f"{achieved_ev_per_sec:.1f} ev/s (target {_INBOX_TARGET_EV_PER_SEC} ev/s, 비차단); "
        f"round trips={len(queries)} (budget=={expected_round_trips}), "
        f"정규화 게이트={normalized_max_elapsed_sec:.3f}s"
    )
    assert processed == _INBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"inbox 백로그 드레인 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(리뷰를 받으세요)."
    )
    assert elapsed_sec <= normalized_max_elapsed_sec, (
        f"inbox 백로그 드레인({_INBOX_BATCH_SIZE}건) 소요시간({elapsed_sec:.3f}s)이 환경 정규화 "
        f"상한({normalized_max_elapsed_sec:.3f}s)을 넘었습니다."
    )


async def test_inbox_backlog_isolates_one_poisoned_event(pool: asyncpg.Pool) -> None:
    """실패 주입(DEPTH 감사 결손 보강) — 백로그의 마지막 이벤트 하나가 fills
    삽입 단계에서 항상 실패해도(poisoned), `received_at` 오름차순 클레임
    (`inbox_repository.py` §5.1) 덕에 그보다 먼저 들어온 나머지는 전부
    정상 드레인된다. poisoned 이벤트는 트랜잭션이 롤백되어 `NEW` 상태로
    남는다 — 데이터 유실이 아니라 다음 주기 재시도 대상으로 남는 것이
    fail-closed 계약이다(모듈 `inbox_processor.py` "행 롤백, 다음 주기에
    재시도" 참조)."""
    healthy_n = 5
    await _seed_inbox_backlog(pool, healthy_n)

    poisoned_user = await create_test_user(pool)
    poisoned_order_id, poisoned_cid, poisoned_exoid = await insert_open_order(pool, poisoned_user)
    poisoned_event = partial_fill_event(
        exchange_order_id=poisoned_exoid, client_order_id=poisoned_cid
    )
    inbox_repo = InboxRepository()
    async with pool.acquire() as conn:
        inserted = await inbox_repo.insert_if_absent(conn, poisoned_event)
    assert inserted is True

    class _PoisonedFillsRepo(FillsRepository):
        async def insert_if_absent(self, conn: asyncpg.Connection, fill: FillEvent) -> bool:  # type: ignore[override]
            if fill.exchange_order_id == poisoned_exoid:
                raise ConnectionError("simulated poisoned event")
            return await super().insert_if_absent(conn, fill)

    processor = InboxProcessor(pool, fills_repo=_PoisonedFillsRepo())
    processed = await processor.process_once(limit=healthy_n + 1)

    assert processed == healthy_n, (
        "poisoned 이벤트를 제외한 나머지는 배치 실패와 무관하게 전부 처리돼야 합니다."
    )
    async with pool.acquire() as conn:
        poisoned_state = await conn.fetchval(
            "SELECT state FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
            poisoned_event.venue,
            poisoned_event.provider_event_id,
        )
        poisoned_order = await conn.fetchrow(
            "SELECT status, filled_quantity FROM orders WHERE order_id = $1", poisoned_order_id
        )
    assert poisoned_state == "NEW", "poisoned 이벤트는 롤백되어 재시도 대상(NEW)으로 남아야 합니다."
    assert poisoned_order["status"] == "SUBMITTED"
    assert poisoned_order["filled_quantity"] == Decimal("0")
