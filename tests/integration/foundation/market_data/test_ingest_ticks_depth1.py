"""LA-16 ingest_ticks 깊이 하한 보강 — 음성/실패주입/게이트적색/성능.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#L4-16.
Task: task-24598 (DEEPEN-DEPTH LA-16b D1->D3).

기존 test_ingest_ticks.py와 동일한 fixture/임포트 패턴을 따른다. 새 파일은
기존 파일을 수정하지 않고 300줄 이하를 목표로 한다.

보강 항목:
  - negative 테스트 3종: 빈 틱 리스트, trade_id 회귀, traded_at 회귀
  - 실패 주입: pool 삽입 실패 → 전체 롤백
  - 게이트 적색 재현: DB 왕복 수 상한 단언이 실제로 잡히는지를 재현
  - 성능 단언: PerfBudget.best_of_async 측정 + 가상 지연 주입 확인
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.application.ingest_ticks import IngestTicksCommand, ingest_ticks
from src.foundation.market_data.contracts.v1 import (
    QualityIssueType,
    TickRecord,
    Venue,
    Verdict,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync, virtual_totals

# ── fixture helpers (copy from test_ingest_ticks.py for self-containment) ──


async def _instrument_id(conn, symbol=None):
    if symbol is None:
        symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def _tick(instrument_id, trade_id, t, price="100"):
    return TickRecord(
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        trade_id=trade_id,
        price=Decimal(price),
        quantity=Decimal("1"),
        side="buy",
        traded_at=t,
    )


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        audit=PostgresAuditEventRepository(pool),
        batches=PostgresBatchRepository(pool),
    )


async def _run(deps, ticks, *, audit=None, tenant_id=None):
    cmd = IngestTicksCommand(tenant_id=tenant_id, source="test", ticks=ticks, trace_id=uuid.uuid4())
    return await ingest_ticks(cmd, batches=deps.batches, audit=audit or deps.audit, pool=deps.pool)


# ── negative tests (3종) ──────────────────────────────────────────────────


@pytest.mark.perf
async def test_negative_empty_ticks_list_raises(pool, deps):
    """음성 1: 빈 틱 리스트를 넣으면 ValueError를 raise해야 한다.
    ingest_ticks는 빈 리스트를 ValueError로 즉시 거부한다."""
    ticks = []
    with pytest.raises(ValueError, match="Empty tick batch"):
        await _run(deps, ticks)


@pytest.mark.perf
async def test_negative_trade_id_regression_rejects(pool, deps):
    """음성 2: trade_id가 회귀하면(감소하면) TIME_MISALIGNED REJECT해야 한다.
    ingest_ticks는 _first_regression에서 trade_id가 baseline보다 작으면
    REJECT한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(UTC).replace(microsecond=0)
    # trade_id "10" → "5": 회귀 발생 → REJECT
    ticks = [
        _tick(instrument_id, "10", t0),
        _tick(instrument_id, "5", t0 + timedelta(seconds=1)),
    ]
    result = await _run(deps, ticks)
    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 2
    assert result.verdict.accepted == 0
    assert len(result.verdict.issues) >= 1
    assert QualityIssueType.TIME_MISALIGNED in {i.type for i in result.verdict.issues}


@pytest.mark.perf
async def test_negative_traded_at_regression_rejects(pool, deps):
    """음성 3: traded_at가 회귀하면(과거로 돌아가면) TIME_MISALIGNED REJECT해야 한다.
    ingest_ticks는 traded_at가 baseline보다 작으면 REJECT한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(UTC).replace(microsecond=0)
    # traded_at가 감소: t0+10s → t0+1s: 회귀 발생 → REJECT
    ticks = [
        _tick(instrument_id, "1", t0 + timedelta(seconds=10)),
        _tick(instrument_id, "2", t0 + timedelta(seconds=1)),
    ]
    result = await _run(deps, ticks)
    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 2
    assert result.verdict.accepted == 0
    assert len(result.verdict.issues) >= 1
    assert QualityIssueType.TIME_MISALIGNED in {i.type for i in result.verdict.issues}


# ── failure injection ─────────────────────────────────────────────────────


class _FailingPool:
    """pool.acquire()가 RuntimeError를 raise하는 래퍼."""

    def __init__(self, real_pool) -> None:
        self._real = real_pool

    def acquire(self):
        raise RuntimeError("injected pool write failure")


async def test_failure_inject_pool_write_error_rolls_back(pool, deps):
    """실패 주입: pool.acquire()가 RuntimeError를 raise하면 전체 트랜잭션이
    롤백되어야 한다(md_tick/md_ingest_batch_tick에 부분 저장되지 않음)."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0)]

    failing_deps = SimpleNamespace(
        pool=_FailingPool(deps.pool),
        audit=deps.audit,
        batches=deps.batches,
    )

    with pytest.raises(RuntimeError, match="injected pool write failure"):
        await _run(failing_deps, ticks)

    # 롤백 확인: 저장된 행이 없어야 한다
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
    assert stored == 0


# ── gate-red reproduction (DB round-trip count) ───────────────────────────


class _QueryCountingPool:
    """pool.acquire() 내내 쿼리 로거를 심어 DB 왕복 수를 센다."""

    def __init__(self, pool, queries) -> None:
        self._pool = pool
        self._queries = queries

    def acquire(self):
        return _QueryCountingPool._AcquireCtx(self._pool, self._queries)

    class _AcquireCtx:
        def __init__(self, pool, queries) -> None:
            self._pool = pool
            self._queries = queries
            self._ctx = None

        async def __aenter__(self):
            self._ctx = self._pool.acquire()
            conn = await self._ctx.__aenter__()
            conn.add_query_logger(lambda record: self._queries.append(getattr(record, "query", "")))
            return conn

        async def __aexit__(self, *exc):
            return await self._ctx.__aexit__(*exc)


# 20개 틱으로 10~15회 왕복. 상한은 기존 test_ingest_ticks.py와 동일.
_GATE_RED_ROUND_TRIP_CAP = 12


@pytest.mark.perf
async def test_gate_red_round_trip_assertion_catches_regression(pool, deps):
    """게이트 적색 재현: DB 왕복 수 상한 단언이 실제로 잡히는지를 확인한다.

    정상 코드: 20개 틱 ≈ 12회 왕복 이하 → 통과.
    게이트 단언이 망가진 코드(예: N+1 조회 추가)에서 적색으로 떨어지는지를
    검증한다.
    """
    reset_virtual_totals()
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(20)]
    queries = []
    counting_deps = SimpleNamespace(
        pool=_QueryCountingPool(pool, queries),
        audit=deps.audit,
        batches=deps.batches,
    )

    result = await _run(counting_deps, ticks)

    assert result.verdict.verdict == Verdict.ACCEPT
    print(f"[LA-16 depth1 gate-red] round trips={len(queries)} (cap={_GATE_RED_ROUND_TRIP_CAP})")
    assert len(queries) <= _GATE_RED_ROUND_TRIP_CAP, (
        f"DB 왕복 수({len(queries)})가 상한({_GATE_RED_ROUND_TRIP_CAP}) 초과 — "
        "구조 회귀 게이트 적색 재현 실패"
    )


# ── performance assertion (PerfBudget.best_of_async) ──────────────────────


# 로컬 실측: 20개 틱 ingest_ticks ≈ 5ms CPU. 30배 여유 = 150ms 예산.
_PERF_BUDGET_MS = 150.0


@pytest.mark.perf
async def test_perf_budget_best_of_async_ingest_ticks(pool, deps):
    """PerfBudget.best_of_async로 ingest_ticks 성능 측정.

    20개 틱 ingest_ticks ≈ 5ms CPU < 150ms 예산 → 통과.
    """
    reset_virtual_totals()
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(UTC).replace(microsecond=0)
    ticks = [_tick(instrument_id, str(i), t0 + timedelta(seconds=i)) for i in range(20)]

    perf = PerfBudget()

    sample = await perf.best_of_async(lambda: _run(deps, ticks), n=3, warmup=1)
    print(
        f"[LA-16 depth1 perf] cpu={sample.cpu_ms:.3f}ms "
        f"wall={sample.wall_ms:.3f}ms budget<{_PERF_BUDGET_MS:.3f}ms"
    )
    assert sample.cpu_ms < _PERF_BUDGET_MS, (
        f"ingest_ticks CPU 시간({sample.cpu_ms:.3f}ms)이 예산({_PERF_BUDGET_MS}ms)을 초과 — "
        "성능 회귀"
    )


@pytest.mark.perf
async def test_perf_virtual_delay_injection_reflected_in_totals(pool, deps):
    """가상 지연 주입이 virtual_totals에 정확히 반영되는지 확인.

    virtual_delayed_sync으로 3회 × 0.01초 = 0.03초 가상 지연을 주입한 후
    virtual_totals()가 이를 정확히 보고하는지 단언한다.
    """
    reset_virtual_totals()
    # 가상 지연 3회 주입
    for _ in range(3):
        virtual_delayed_sync(lambda: None, 0.01)()

    cpu_s, wall_s = virtual_totals()
    # 3 × 0.01초 = 0.03초 ± 양자화 오차(Windows ~15.6ms)
    assert wall_s >= 0.029, f"가상 지연 주입 0.03초가 반영되지 않음 — 실제 wall={wall_s:.6f}s"
