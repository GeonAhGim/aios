"""LB-19 / FA-6: query and latency budgets."""

from __future__ import annotations

import math
import time
from decimal import Decimal

import pytest

from src.api.deps import get_pool
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.main import app
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.support.positions_router import (
    BASE,
    _create_account,
    _open_position,
    _register,
)
from tests.support.positions_router import client as client
from tests.support.positions_router import pool as pool


class _QueryCountingConnectionCtx:
    """`pool.acquire()`의 async 컨텍스트 프록시 -- 실 connection에 query
    logger를 달아 라우터가 이 요청 하나에 실제로 여는 SQL 왕복 수를 센다
    (test_rebuild_snapshot.py f80af78e와 동일 기법)."""

    def __init__(self, inner_ctx, sink: list[str]) -> None:
        self._inner_ctx = inner_ctx
        self._sink = sink
        self._conn = None
        self._log = None

    async def __aenter__(self):
        self._conn = await self._inner_ctx.__aenter__()
        self._log = lambda record: self._sink.append(getattr(record, "query", ""))
        self._conn.add_query_logger(self._log)
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if self._conn is not None and self._log is not None:
            self._conn.remove_query_logger(self._log)
        return await self._inner_ctx.__aexit__(exc_type, exc, tb)



class _QueryCountingPool:
    def __init__(self, pool) -> None:
        self._pool = pool
        self.queries: list[str] = []

    def acquire(self) -> _QueryCountingConnectionCtx:
        return _QueryCountingConnectionCtx(self._pool.acquire(), self.queries)



_ACCOUNT_COUNT = 5



_MAX_ROUND_TRIPS = _ACCOUNT_COUNT + 3  # 1(owned account ids) + N(list_open) + 여유분



_MAX_LATENCY_MS = 3000.0



@pytest.mark.perf
async def test_list_positions_round_trip_and_latency_guard(client, pool):
    """수치 성능 단언 -- `list_positions`는 계정별로 순차 `list_open` 왕복을
    낸다(§9 LB-17 문서화된 N+1). 계정 수가 늘어도 왕복 수가 선형 상한
    안에 있는지(회귀 가드)와, 공유 TEST_DATABASE_URL이 계속 자라는 환경에서도
    버틸 넉넉한 지연 sanity 상한(절대 임계 대신, task-2959/2962/2970/2977과
    동일 결정)을 함께 잰다."""
    headers, tenant_id = await _register(client)
    for _ in range(_ACCOUNT_COUNT):
        account_id = await _create_account(pool, tenant_id)
        await _open_position(
            pool, tenant_id=tenant_id, account_id=account_id, quantity=Decimal("1")
        )

    counting_pool = _QueryCountingPool(pool)
    app.dependency_overrides[get_pool] = lambda: counting_pool
    try:
        started = time.monotonic()
        response = await client.get(BASE, headers=headers)
        elapsed_ms = (time.monotonic() - started) * 1000
    finally:
        app.dependency_overrides.pop(get_pool, None)

    assert response.status_code == 200
    assert len(response.json()["data"]["items"]) == _ACCOUNT_COUNT
    assert 0 < len(counting_pool.queries) <= _MAX_ROUND_TRIPS, counting_pool.queries
    assert elapsed_ms <= _MAX_LATENCY_MS, elapsed_ms



@pytest.mark.perf
async def test_list_positions_portfolio_id_p95_latency_stays_within_normalized_ceiling(
    client, pool
):
    """수치 성능 단언 -- 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신, 가벼운 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(task-2993/3009와 동일 교훈). `portfolio_id` 경로는
    `resolve_portfolio_scope`가 추가하는 3회 라운드트립만큼 무변경 경로보다
    비용이 늘어야 정상이므로, 그 고정 비용이 회귀로 자라는지 감시한다."""
    headers, tenant_id = await _register(client)
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    account_id = await _create_account(pool, tenant_id)
    await _open_position(
        pool,
        tenant_id=tenant_id,
        account_id=account_id,
        quantity=Decimal("1"),
        portfolio_id=hierarchy.portfolio.portfolio_id,
    )

    async def _call() -> float:
        started = time.monotonic()
        response = await client.get(
            BASE, headers=headers, params={"portfolio_id": str(hierarchy.portfolio.portfolio_id)}
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        return elapsed

    baseline_elapsed = await _call()
    samples = sorted([await _call() for _ in range(20)])
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"GET /positions?portfolio_id=... p95 지연 {p95:.4f}s가 정규화 상한 "
        f"{ceiling:.4f}s(baseline {baseline_elapsed:.4f}s)를 초과했습니다 -- "
        "resolve_portfolio_scope 라운드트립 회귀 의심"
    )
