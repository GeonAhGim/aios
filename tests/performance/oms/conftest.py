"""tests/performance/oms/ 공용 픽스처 — L4-28 §7.1 4종 성능 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§9 L4-28(§7.1 목표 표).
선례: tests/integration/foundation/ledger/test_perf_journal.py(task-1038
esc-ci-d5723ce4366d 종결 — 절대 지연은 print 비차단, 순차 DB 왕복 수만 차단
게이트), tests/performance/test_pre_trade_latency.py(R-57, task-1521 동일
decision).

`pool`을 `max_size=1`로 고정하는 이유 — 이 파일들의 측정 대상(`submit_order`,
`InboxProcessor.ingest`)은 한 논리 호출 안에서 `pool.acquire()/release()`를
여러 번 반복한다(예: `submit_order`의 `verify_entity_context` 4회 + 메인
tx 1회). 왕복 수를 `add_query_logger`로 정확히 세려면 그 반복이 항상 같은
물리 커넥션을 돌려받아야 한다 — 커넥션이 여럿인 풀에서는 어느 호출이 어느
커넥션을 받을지 보장할 수 없어 로거가 일부 왕복을 놓칠 수 있다. 이 파일들은
전부 단일 스레드 순차 측정이라 `max_size=1`이 동시성을 제한하지 않는다.

`_drain_shared_queues`(autouse) — `order_command_outbox`/`provider_event_
inbox`의 클레임 쿼리는 전역 큐다(주문/테스트로 필터하지 않음, §5.1/L4-15
설계 그대로). 공유 `TEST_DATABASE_URL`에 이전 pytest 실행이 남긴 PENDING/
NEW 잔여 행이 있으면 이 디렉터리의 정확 왕복 수 단언("방금 넣은 행만
클레임된다"는 전제)이 깨지므로, 각 테스트 시작 전에 두 큐를 비운다
(`_fixtures.drain_outbox_backlog`/`drain_inbox_backlog`).
"""
from __future__ import annotations

import os
import time

import asyncpg
import pytest

from tests.performance.oms._fixtures import drain_inbox_backlog, drain_outbox_backlog


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=1)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _drain_shared_queues(pool: asyncpg.Pool) -> None:
    await drain_outbox_backlog(pool)
    await drain_inbox_backlog(pool)


async def attach_round_trip_logger(pool: asyncpg.Pool) -> list[str]:
    """이 풀의 유일한 물리 커넥션에 쿼리 로거를 붙이고, 이후 그 커넥션을 거치는
    모든 쿼리가 쌓일 리스트를 돌려준다(호출부가 측정 직전에 `.clear()`해서
    쓴다 — `test_perf_journal.py`의 `_count_append_round_trips`와 동일 관례).
    """
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    conn = await pool.acquire()
    conn.add_query_logger(_log)
    await pool.release(conn)
    return queries


async def measure_baseline_round_trip_p95_ms(
    pool: asyncpg.Pool, *, warmup: int = 5, samples: int = 50
) -> float:
    """이 환경의 기준 DB 왕복비용(pool.acquire + BEGIN/COMMIT + SELECT 1) p95
    — `test_perf_journal.py`와 동일 대조군(그 파일 모듈 docstring 참조)."""
    samples_ms: list[float] = []
    for _ in range(warmup + samples):
        started = time.perf_counter()
        async with pool.acquire() as conn, conn.transaction():
            await conn.fetchval("SELECT 1")
        samples_ms.append((time.perf_counter() - started) * 1000)
    samples_ms = samples_ms[warmup:]
    samples_ms.sort()
    return samples_ms[int(len(samples_ms) * 0.95)]
