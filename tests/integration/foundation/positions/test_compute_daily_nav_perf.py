"""LB-15 `compute_daily_nav` 수치 성능 단언(D3) — 순차 DB 왕복 수 상한.

`test_compute_daily_nav.py`에서 분리(task-10197, 675줄 LOC 규율 초과).
`tests/integration/foundation/positions/test_perf_journal_append.py`
(LB-18)와 동일 기법(asyncpg 쿼리 로거)으로 `compute_daily_nav` 1회가 쓰는
순차 DB 왕복 수를 직접 세어 구조 회귀를 막는다. 절대 지연은 실행환경
(네트워크/디스크)에 선형 비례해 흔들리므로(task-822/1059 decision과 동일
이유) 게이트로 쓰지 않고 참고용으로만 print한다 — 왕복 수 상한만 차단
게이트로 남긴다.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.compute_daily_nav import compute_daily_nav
from tests.integration.foundation.positions._compute_daily_nav_fixtures import (
    BITGET,
    NOW,
    FakeCashSource,
    FakeFxRateSource,
    cmd,
    setup_account,
)


class _CountingAcquireContext:
    def __init__(self, inner: Any, queries: list[str]) -> None:
        self._inner = inner
        self._queries = queries
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> asyncpg.Connection:
        self._conn = await self._inner.__aenter__()
        self._conn.add_query_logger(self._log)
        return self._conn

    def _log(self, record: object) -> None:
        self._queries.append(str(getattr(record, "query", "")))

    async def __aexit__(self, *exc_info: object) -> object:
        assert self._conn is not None
        self._conn.remove_query_logger(self._log)
        return await self._inner.__aexit__(*exc_info)


class _CountingPool:
    """실제 `pool`을 감싸 `compute_daily_nav` 한 회 호출이 소비하는 순차 DB
    왕복 수를 센다(`test_perf_journal_append.py`/LB-18과 동일 기법)."""

    def __init__(self, pool: asyncpg.Pool, queries: list[str]) -> None:
        self._pool = pool
        self._queries = queries

    def acquire(self) -> _CountingAcquireContext:
        return _CountingAcquireContext(self._pool.acquire(), self._queries)


_PERF_SAMPLE_COUNT = 30
_MAX_SEQUENTIAL_ROUND_TRIPS = 3  # list_open + nav_repo.get(prev) + nav_repo.insert


@pytest.mark.perf
async def test_compute_daily_nav_sequential_round_trips_and_latency(pool: asyncpg.Pool) -> None:
    """수치 성능 단언(D3) — `compute_daily_nav` 1회가 쓰는 순차 DB 왕복 수를
    직접 세어 구조 회귀를 막는다."""
    tenant_id, account_id = await setup_account(pool)
    cash = FakeCashSource()
    cash.seed(account_id, Decimal("1000"))
    nav_repo = PostgresNavRepository(pool)

    queries: list[str] = []
    await compute_daily_nav(
        cmd(tenant_id=tenant_id, account_id=account_id, at=NOW),
        snapshots=PostgresSnapshotRepository(pool),
        cash=cash,
        nav_repo=nav_repo,
        calendar=BITGET,
        fx=FakeFxRateSource(),
        pool=_CountingPool(pool, queries),
    )
    round_trip_count = len(queries)

    latencies_ms: list[float] = []
    for _ in range(_PERF_SAMPLE_COUNT):
        tenant_i, account_i = await setup_account(pool)
        cash_i = FakeCashSource()
        cash_i.seed(account_i, Decimal("1000"))
        started = time.perf_counter()
        await compute_daily_nav(
            cmd(tenant_id=tenant_i, account_id=account_i, at=NOW),
            snapshots=PostgresSnapshotRepository(pool),
            cash=cash_i,
            nav_repo=nav_repo,
            calendar=BITGET,
            fx=FakeFxRateSource(),
            pool=pool,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    print(
        f"\ncompute_daily_nav sequential DB round trips={round_trip_count} "
        f"(max={_MAX_SEQUENTIAL_ROUND_TRIPS}); "
        f"latency p95={p95_ms:.3f}ms (n={_PERF_SAMPLE_COUNT})"
    )

    assert round_trip_count <= _MAX_SEQUENTIAL_ROUND_TRIPS, (
        f"compute_daily_nav 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_SEQUENTIAL_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
