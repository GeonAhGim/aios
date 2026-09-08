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
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pytest

from src.services.oms.adapters.inbox_repository import InboxRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from tests.integration.oms.conftest import create_test_user, insert_order
from tests.performance.oms._fixtures import insert_open_order, partial_fill_event
from tests.performance.oms.conftest import attach_round_trip_logger
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate, submit_payload

_OUTBOX_TARGET_CMD_PER_SEC = 100.0  # §7.1 운영 목표 — 비차단(print)
_OUTBOX_BATCH_SIZE = 100
_OUTBOX_FIXED_ROUND_TRIPS = 4  # claim_batch tx(BEGIN+UPDATE+COMMIT+리셋)
_OUTBOX_PER_ROW_ROUND_TRIPS = 24  # test_outbox_dispatch_latency.py 실측(28-4)

_INBOX_TARGET_EV_PER_SEC = 500.0  # §7.1 운영 목표 — 비차단(print)
_INBOX_BATCH_SIZE = 500
_INBOX_PER_ROW_ROUND_TRIPS = 20  # process_once 부분체결 1행당 실측


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
    queries = await attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    report = await dispatcher.dispatch_once(limit=_OUTBOX_BATCH_SIZE)
    elapsed_sec = time.perf_counter() - started

    achieved_cmd_per_sec = _OUTBOX_BATCH_SIZE / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = (
        _OUTBOX_FIXED_ROUND_TRIPS + _OUTBOX_BATCH_SIZE * _OUTBOX_PER_ROW_ROUND_TRIPS
    )
    print(
        f"\noutbox single-worker throughput: {report.claimed} claimed / "
        f"{report.acknowledged} acknowledged in {elapsed_sec:.3f}s = "
        f"{achieved_cmd_per_sec:.1f} cmd/s (target {_OUTBOX_TARGET_CMD_PER_SEC} cmd/s, 비차단 — "
        "task-1038/1521 decision); "
        f"sequential DB round trips={len(queries)} (budget=={expected_round_trips})"
    )
    assert report.claimed == _OUTBOX_BATCH_SIZE
    assert report.acknowledged == _OUTBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"outbox 배치 처리 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(모듈 docstring 공식을 갱신하고 리뷰를 받으세요)."
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
    queries = await attach_round_trip_logger(pool)
    queries.clear()

    started = time.perf_counter()
    processed = await processor.process_once(limit=_INBOX_BATCH_SIZE)
    elapsed_sec = time.perf_counter() - started

    achieved_ev_per_sec = processed / elapsed_sec if elapsed_sec > 0 else float("inf")
    expected_round_trips = _INBOX_BATCH_SIZE * _INBOX_PER_ROW_ROUND_TRIPS
    print(
        f"\ninbox backlog drain throughput: {processed} processed in {elapsed_sec:.3f}s = "
        f"{achieved_ev_per_sec:.1f} ev/s (target {_INBOX_TARGET_EV_PER_SEC} ev/s, 비차단 — "
        "task-1038/1521 decision); "
        f"sequential DB round trips={len(queries)} (budget=={expected_round_trips})"
    )
    assert processed == _INBOX_BATCH_SIZE
    assert len(queries) == expected_round_trips, (
        f"inbox 백로그 드레인 순차 DB 왕복 수({len(queries)})가 예산({expected_round_trips})과 "
        "다릅니다 — 배치 처리 구조 변경입니다(모듈 docstring 공식을 갱신하고 리뷰를 받으세요)."
    )
    # 절대 처리량(ev/s)은 게이트로 쓰지 않는다(모듈 docstring, esc-826/task-1038 decision).
