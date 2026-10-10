"""LB-13 재빌드의 DB 왕복 수와 지연 예산 검증."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.application.rebuild_snapshot import (
    rebuild_snapshot,
)
from tests.integration.foundation.positions.conftest import (
    force_row_replace,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    _clock,
    _fill,
    _open,
    _RealPorts,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    ports as ports,
)


class _QueryCountingConnectionCtx:
    """`pool.acquire()`의 async 컨텍스트 프록시 -- 내부 connection에 query
    logger를 달아 rebuild_snapshot()이 자체적으로 여는 왕복 수를 센다
    (`test_perf_journal_append.py`/`test_executor.py` ce2ce8ce와 동일 기법,
    단 rebuild_snapshot은 record_fill_in_position_ledger처럼 connection을
    노출하지 않고 자체 pool.acquire()를 여는 운영 도구라 pool 자체를 얇게
    감싼다)."""

    def __init__(self, inner_ctx: Any, sink: list[str]) -> None:
        self._inner_ctx = inner_ctx
        self._sink = sink
        self._conn: Any = None
        self._log: Any = None

    async def __aenter__(self) -> Any:
        self._conn = await self._inner_ctx.__aenter__()
        self._log = lambda record: self._sink.append(getattr(record, "query", ""))
        self._conn.add_query_logger(self._log)
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        if self._conn is not None and self._log is not None:
            self._conn.remove_query_logger(self._log)
        return await self._inner_ctx.__aexit__(exc_type, exc, tb)


class _QueryCountingPool:
    """rebuild_snapshot(pool, ...)가 유일하게 쓰는 `pool.acquire()`만 감싼다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.queries: list[str] = []

    def acquire(self) -> _QueryCountingConnectionCtx:
        return _QueryCountingConnectionCtx(self._pool.acquire(), self.queries)


_MAX_REBUILD_ROUND_TRIPS = 8
_MAX_REBUILD_LATENCY_MS = 2000.0


@pytest.mark.perf
async def test_rebuild_snapshot_round_trip_and_latency_guard(
    pool: asyncpg.Pool, ports: _RealPorts, perf_budget
) -> None:
    """수치 성능 단언(DEEPEN task-2958) — DEPTH 감사(task-2723,
    docs/audit/DEPTH_LA_LB_LC.md #452)가 원 리프(2c9bf78)에 이 축 증빙이
    전무하다고 판정했다. task-2959/2962/2970/2974/2977과 같은 결정을
    따른다: 공유 CI 환경의 절대 지연은 이 파일이 통제할 수 없는 변동성을
    낳으므로, 구조 회귀 가드로 rebuild_snapshot() 1회(drift 존재 + 실제
    반영)의 순차 DB 왕복 수 상한(lock + get + list_for + upsert(쿼리 4건)
    + 쿼리 로거가 트랜잭션 경계(BEGIN/COMMIT)도 왕복으로 잡는다는 점까지
    합쳐 실측 6회, 여유 2 -> 8)을 걸고, 지연은 "무한정 걸리지 않는다"는
    느슨한 sanity 상한만 건다.

    raw `time.perf_counter()` 단언을 `perf_budget.sample_async`(task-11626,
    `tests/conftest.py` PerfBudget)로 전환 — coverage tracer 일시정지 +
    wall_ms 계측을 공용 헬퍼로 위임한다. DB 왕복이라 `cpu_ms`(process_time)
    는 대기 시간을 보지 못하므로 `wall_ms`를 기준으로 삼는다. `sample_async`
    는 `fn()`을 정확히 1회만 호출하므로(이 테스트는 drift가 1회만 존재하는
    상태 의존적 시나리오라 재호출이 불가능하다) 기존과 동일하게 단일
    실행만 측정한다.
    """

    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
    )

    counting_pool = _QueryCountingPool(pool)

    async def _rebuild() -> Any:
        return await rebuild_snapshot(
            position_key,
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=counting_pool,
            clock=_clock,
            dry_run=False,
        )

    sample = await perf_budget.sample_async(_rebuild)
    report = sample.result
    elapsed_ms = sample.wall_ms
    round_trip_count = len(counting_pool.queries)

    print(
        f"\nrebuild_snapshot latency={elapsed_ms:.3f}ms "
        f"(sanity max={_MAX_REBUILD_LATENCY_MS}ms); "
        f"sequential DB round trips={round_trip_count} (max={_MAX_REBUILD_ROUND_TRIPS})"
    )
    assert report.applied is True
    assert round_trip_count <= _MAX_REBUILD_ROUND_TRIPS, (
        f"rebuild_snapshot 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_REBUILD_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    assert elapsed_ms < _MAX_REBUILD_LATENCY_MS, (
        f"rebuild_snapshot 지연({elapsed_ms:.1f}ms)이 sanity 상한"
        f"({_MAX_REBUILD_LATENCY_MS}ms)을 초과했습니다."
    )
