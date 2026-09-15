"""LA-18 quality_metrics/scheduler 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-18.
DoD(task-712): 스케줄러 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 차단
안 함.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import AssetClass
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.quality_metrics import export_quality_metrics
from src.foundation.market_data.application.register_instrument import (
    apply_lifecycle_event,
    register_instrument,
)
from src.foundation.market_data.application.scheduler import (
    MarketDataQualityScheduler,
    WatchedSeries,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestCandlesCommand,
    LifecycleEventCommand,
    RegisterInstrumentCommand,
    SeriesKey,
    Timeframe,
    Venue,
)
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        refs=PostgresReferenceRepository(pool),
        cal=PostgresCalendarRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        store=PostgresCandleStore(pool),
        batches=PostgresBatchRepository(pool),
    )


def _bitget_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


def _candle(t: datetime, o: str, h: str, low: str, c: str, v: str) -> CandleRecord:
    return CandleRecord(
        key=SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1),
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _clock(t0: datetime):
    def clock() -> datetime:
        return t0

    return clock


class _FakeIngestSource:
    def __init__(self, candles: list[CandleRecord]) -> None:
        self._candles = candles

    async def fetch_candles(self, venue, raw_symbol, tf, start, end):
        return list(self._candles)


class _FlakyCandleStore:
    """실제 `CandleStore`를 감싸되, 지정된 `instrument_id`에 대해서만
    `last_open_time`이 예외를 던진다 — 시계열 하나의 계산 실패를
    주입하기 위한 테스트 전용 래퍼(§9 LA-18 DoD)."""

    def __init__(self, inner: PostgresCandleStore, boom_instrument_id: uuid.UUID) -> None:
        self._inner = inner
        self._boom = boom_instrument_id

    async def last_open_time(self, conn, key):
        if key.instrument_id == self._boom:
            raise RuntimeError("injected store failure")
        return await self._inner.last_open_time(conn, key)

    async def upsert_batch(self, conn, batch_id, candles):
        return await self._inner.upsert_batch(conn, batch_id, candles)

    async def quarantine(self, conn, batch_id, candles, issues):
        return await self._inner.quarantine(conn, batch_id, candles, issues)

    async def query(self, conn, key, start, end, as_of):
        return await self._inner.query(conn, key, start, end, as_of)


async def _listed_instrument(deps):
    listed_at = datetime.now(timezone.utc) - timedelta(days=1)
    cmd = RegisterInstrumentCommand(
        venue=Venue.BITGET,
        venue_symbol=_bitget_symbol(),
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        listed_at=listed_at,
        actor_subject_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id,
            event="LIST",
            effective_at=datetime.now(timezone.utc),
            source_ref="test:list",
            actor_subject_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
        ),
        current=instrument,
        refs=deps.refs,
        audit=deps.audit,
    )


async def _ingest(deps, instrument, candles, *, start, end, at):
    from src.foundation.market_data.application.ingest_candles import ingest_candles

    cmd = IngestCandlesCommand(
        tenant_id=None,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=start,
        range_end=end,
        trace_id=uuid.uuid4(),
    )
    return await ingest_candles(
        cmd,
        source=_FakeIngestSource(candles),
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=deps.pool,
        clock=_clock(at),
    )


async def test_export_quality_metrics_reports_gauge_after_one_cycle(deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [
        _candle(t0, "100", "110", "90", "105", "10"),
        _candle(t0 + timedelta(minutes=1), "100", "110", "90", "105", "10"),
    ]
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=2), at=t0)

    later = t0 + timedelta(minutes=10)
    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(later),
    )

    mine = [m for m in results if m.key.instrument_id == instrument.instrument_id]
    assert len(mine) == 1
    metric = mine[0]
    assert metric.staleness_s == int((later - (t0 + timedelta(minutes=1))).total_seconds())
    assert metric.gap_ratio_24h == Decimal("0")

    gauge = registry.gauge("md_staleness_seconds", ("venue", "instrument_id", "timeframe"))
    sample = gauge.samples()[("BITGET", str(instrument.instrument_id), "1m")]
    assert sample == float(metric.staleness_s)


async def test_export_quality_metrics_isolates_one_symbol_failure(deps):
    boom_instrument = await _listed_instrument(deps)
    healthy_instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]
    for instrument in (boom_instrument, healthy_instrument):
        await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0)

    flaky_store = _FlakyCandleStore(deps.store, boom_instrument.instrument_id)
    later = t0 + timedelta(minutes=5)
    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=flaky_store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(later),
    )

    result_ids = {m.key.instrument_id for m in results}
    assert boom_instrument.instrument_id not in result_ids, "실패한 심볼은 결과에서 빠진다"
    assert healthy_instrument.instrument_id in result_ids, "나머지 심볼은 차단되지 않는다"


async def test_export_quality_metrics_computes_gap_and_reject_ratio(deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = lambda t: _candle(t, "100", "110", "90", "105", "10")  # noqa: E731
    bad = _candle(t0 + timedelta(minutes=4), "100", "90", "80", "85", "10")  # high(90) < open(100)
    candles = [
        good(t0),
        good(t0 + timedelta(minutes=1)),
        good(t0 + timedelta(minutes=2)),
        good(t0 + timedelta(minutes=3)),
        bad,
        good(t0 + timedelta(minutes=6)),
    ]  # minute 5 빠짐 → GAP 1건, minute 4는 REJECT 1건 — 총 6건 중 1건 rejected(<20%)
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=7), at=t0)

    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(t0 + timedelta(minutes=10)),
    )

    mine = next(m for m in results if m.key.instrument_id == instrument.instrument_id)
    assert mine.gap_ratio_24h == Decimal(1) / Decimal(7)
    assert mine.reject_ratio_24h == Decimal(1) / Decimal(6)


async def test_export_quality_metrics_skips_series_with_no_stored_candles(deps):
    """REJECT 비율 > 20%면 배치 전체가 QUARANTINE되어 `md_ingest_batch`는
    생기지만 `md_candle`은 비어 있다(§4.1 "부분 저장 금지") — `_active_series`는
    이 시계열을 활성으로 보지만 `last_open_time`이 None이라 스킵해야 하고,
    이 스킵이 다른 시계열 처리를 막지 않아야 한다."""
    quarantined_instrument = await _listed_instrument(deps)
    healthy_instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    all_bad = [
        _candle(t0 + timedelta(minutes=i), "100", "90", "80", "85", "10")  # high<open → REJECT
        for i in range(3)
    ]
    good = [_candle(t0, "100", "110", "90", "105", "10")]
    await _ingest(
        deps, quarantined_instrument, all_bad, start=t0, end=t0 + timedelta(minutes=3), at=t0
    )
    await _ingest(deps, healthy_instrument, good, start=t0, end=t0 + timedelta(minutes=1), at=t0)

    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=registry,
        clock=_clock(t0 + timedelta(minutes=10)),
    )

    result_ids = {m.key.instrument_id for m in results}
    assert quarantined_instrument.instrument_id not in result_ids, (
        "저장된 캔들이 없는 시계열(전량 QUARANTINE)은 결과에서 빠진다"
    )
    assert healthy_instrument.instrument_id in result_ids, "나머지 시계열은 계속 처리된다"


async def test_scheduler_run_once_isolates_ingest_failure_and_exports_metrics(deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]

    scheduler = MarketDataQualityScheduler(
        deps.pool,
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        registry=MetricsRegistry(),
        source=_FakeIngestSource(candles),
        audit=deps.audit,
        watched=[
            WatchedSeries(
                venue=Venue.BITGET,
                canonical_symbol=instrument.canonical_symbol,
                timeframe=Timeframe.M1,
                lookback=timedelta(minutes=5),
            ),
            WatchedSeries(
                venue=Venue.BITGET,
                canonical_symbol="UNKNOWN-NOT-REGISTERED",
                timeframe=Timeframe.M1,
                lookback=timedelta(minutes=5),
            ),
        ],
        clock=_clock(t0 + timedelta(minutes=1)),
    )

    report = await scheduler.run_once()

    assert len(report.ingested) == 1
    assert len(report.ingest_failed) == 1, "등록되지 않은 심볼 하나는 실패해야 한다"
    metric_ids = {m.key.instrument_id for m in report.metrics}
    assert instrument.instrument_id in metric_ids, "실패한 대상이 게이지 export를 막지 않는다"


# --- DEEPEN task-2981 (docs/audit/DEPTH_LA_LB_LC.md#712) — 이 리프의 D3 하한
# 미달 3건(수치 성능/지연/round-trip 단언 없음; 문서화된 실사고 연계 게이트
# 적색 재현 없음; 적대적/replay/동시성 증명 없음)을 채운다. ---


class _TenantBlindBatchRepository:
    """`batches.get()`에 실제 소유 tenant_id 대신 항상 `None`을 넘기는 결함
    시뮬레이션 — quality_metrics.py 모듈 docstring(105-109행, LA-22 편차)이
    문서화한 위험의 재현. LA-22(commit 190dfea4)는 `BatchRepository.get()`에
    `tenant_id IS NOT DISTINCT FROM $2` fencing을 넣어 교차 tenant 열람을
    막았는데(postgres_batch_repository.py 164-168행 docstring), 그 부작용으로
    "tenant 불일치"와 "행 자체가 없음"이 같은 `None` 반환으로 합쳐져
    존재를 숨긴다. `_latest_batch`가 되돌려주는 실제 tenant_id를 그대로
    쓰지 않고 이 래퍼처럼 아무 값(None)이나 넘기면, tenant 소유 배치가
    분명히 있는데도 `get()`이 조용히 None을 반환해 gap/reject 비율이
    실제로는 위험한데도 0(거짓 초록)으로 보고된다."""

    def __init__(self, inner: PostgresBatchRepository) -> None:
        self._inner = inner

    async def get(self, conn, batch_id, tenant_id):  # noqa: ANN001, ARG002
        return await self._inner.get(conn, batch_id, None)


async def test_export_quality_metrics_gate_red_if_tenant_id_not_threaded_to_batches_get(
    deps,
) -> None:
    """게이트 적색 재현(문서화된 실사고 연계, task-825/LA-22) — tenant 소유
    배치의 REJECT 비율이 tenant_id를 올바르게 스레딩했을 때만 정확히
    보이고, 스레딩을 빠뜨리면(흔한 실수 하나) 같은 데이터에서 거짓으로
    0(초록)이 보고됨을 대조한다."""
    tenant_id = await create_test_tenant(deps.pool)
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = lambda t: _candle(t, "100", "110", "90", "105", "10")  # noqa: E731
    bad = _candle(t0 + timedelta(minutes=3), "100", "90", "80", "85", "10")  # high<open → REJECT
    # 6건 중 1건만 REJECT(<20%) — 배치 전체 QUARANTINE(부분 저장 금지)을
    # 피해야 md_candle에 나머지가 남아 batches.get() 경로까지 도달한다.
    candles = [
        good(t0),
        good(t0 + timedelta(minutes=1)),
        good(t0 + timedelta(minutes=2)),
        bad,
        good(t0 + timedelta(minutes=4)),
        good(t0 + timedelta(minutes=5)),
    ]

    cmd = IngestCandlesCommand(
        tenant_id=tenant_id,
        venue=Venue.BITGET,
        canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1,
        range_start=t0,
        range_end=t0 + timedelta(minutes=6),
        trace_id=uuid.uuid4(),
    )
    from src.foundation.market_data.application.ingest_candles import ingest_candles

    await ingest_candles(
        cmd,
        source=_FakeIngestSource(candles),
        store=deps.store,
        refs=deps.refs,
        cal=deps.cal,
        batches=deps.batches,
        audit=deps.audit,
        pool=deps.pool,
        clock=_clock(t0),
    )

    later = _clock(t0 + timedelta(minutes=10))

    correct = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=MetricsRegistry(),
        clock=later,
    )
    correct_mine = next(m for m in correct if m.key.instrument_id == instrument.instrument_id)
    assert correct_mine.reject_ratio_24h == Decimal(1) / Decimal(6), (
        "올바른 tenant_id 스레딩은 실제 REJECT 비율을 그대로 보고해야 한다"
    )

    blind_batches = _TenantBlindBatchRepository(deps.batches)
    blind = await export_quality_metrics(
        batches=blind_batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=MetricsRegistry(),
        clock=later,
    )
    blind_mine = next(m for m in blind if m.key.instrument_id == instrument.instrument_id)
    assert blind_mine.reject_ratio_24h == Decimal("0"), (
        "tenant_id를 빠뜨리면 존재-비노출 fencing에 걸려 REJECT가 거짓으로 0(초록) 보고된다"
        " — 이 결과가 정상이라는 뜻이 아니라, 실제 결함이 재현됐다는 뜻이다"
    )
    assert blind_mine.last_batch_id == correct_mine.last_batch_id, (
        "last_batch_id는 batches.get()과 무관한 별도 조회(_latest_batch)라 "
        "존재-비노출의 영향을 받지 않는다 — 딱 verdict 내용(gap/reject 비율)만"
        " 거짓 초록으로 가려진다는 것이 이 결함의 위험한 지점이다"
    )


async def test_export_quality_metrics_ratio_round_trips_through_independent_query(deps) -> None:
    """수치 round-trip 단언 — `export_quality_metrics`가 반환한
    gap_ratio_24h/reject_ratio_24h가, 같은 배치를 별도 SELECT로 직접 재조회해
    수동으로 계산한 값과 Decimal 정밀도까지 정확히 일치하는지 검증한다
    (LB-11/task-2956와 동일한 "반환값 vs 독립 재조회" round-trip 패턴)."""
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    good = lambda t: _candle(t, "100", "110", "90", "105", "10")  # noqa: E731
    bad = _candle(t0 + timedelta(minutes=4), "100", "90", "80", "85", "10")  # high<open → REJECT
    candles = [
        good(t0),
        good(t0 + timedelta(minutes=1)),
        good(t0 + timedelta(minutes=2)),
        good(t0 + timedelta(minutes=3)),
        bad,
        good(t0 + timedelta(minutes=6)),
    ]  # minute 5 빠짐 → GAP 1건, minute 4 → REJECT 1건, 총 record 6건
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=7), at=t0)

    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=MetricsRegistry(),
        clock=_clock(t0 + timedelta(minutes=10)),
    )
    mine = next(m for m in results if m.key.instrument_id == instrument.instrument_id)

    async with deps.pool.acquire() as conn:
        accepted_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument.instrument_id
        )
        batch_row = await conn.fetchrow(
            "SELECT id FROM md_ingest_batch WHERE instrument_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            instrument.instrument_id,
        )
        quarantined_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle WHERE batch_id = $1", batch_row["id"]
        )
        gap_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT open_time) FROM md_quality_issue "
            "WHERE batch_id = $1 AND type = 'GAP'",
            batch_row["id"],
        )
        reject_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT open_time) FROM md_quality_issue "
            "WHERE batch_id = $1 AND severity = 'REJECT'",
            batch_row["id"],
        )

    record_count = accepted_count + quarantined_count
    expected_gap_ratio = Decimal(gap_count) / Decimal(gap_count + record_count)
    expected_reject_ratio = Decimal(reject_count) / Decimal(record_count)

    assert mine.gap_ratio_24h == expected_gap_ratio, "gap_ratio가 독립 재조회 계산과 일치해야 한다"
    assert mine.reject_ratio_24h == expected_reject_ratio, (
        "reject_ratio가 독립 재조회 계산과 일치해야 한다"
    )
    assert mine.last_batch_id == batch_row["id"], (
        "last_batch_id가 실제 최신 배치 id와 일치해야 한다"
    )


@pytest.mark.perf
async def test_export_quality_metrics_latency_stays_within_normalized_ceiling(deps) -> None:
    """수치 성능/지연 단언 — 공유 TEST_DATABASE_URL은 계속 자라 타이트한
    절대 지연 임계는 못 쓴다(ledger test_perf_journal.py task-920/1029와
    동일 교훈). 이 환경의 기준 DB 왕복비용(SELECT 1) 대비 넉넉한 배율로
    정규화한 상한만 게이트로 쓰고, 절대 수치는 print로 남긴다."""
    series_count = 15
    instruments = []
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(series_count):
        instrument = await _listed_instrument(deps)
        candles = [_candle(t0, "100", "110", "90", "105", "10")]
        await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0)
        instruments.append(instrument)

    baseline_started = time.perf_counter()
    async with deps.pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    baseline_ms = (time.perf_counter() - baseline_started) * 1000

    later = _clock(t0 + timedelta(minutes=10))
    started = time.perf_counter()
    results = await export_quality_metrics(
        batches=deps.batches,
        store=deps.store,
        cal=deps.cal,
        pool=deps.pool,
        registry=MetricsRegistry(),
        clock=later,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    result_ids = {m.key.instrument_id for m in results}
    for instrument in instruments:
        assert instrument.instrument_id in result_ids

    per_series_ceiling_ms = 500.0
    normalized_ceiling_ms = max(
        series_count * per_series_ceiling_ms, series_count * 20 * max(baseline_ms, 1.0)
    )
    print(
        f"\nexport_quality_metrics latency: {elapsed_ms:.3f}ms for {series_count} series "
        f"(baseline SELECT 1={baseline_ms:.3f}ms; normalized ceiling={normalized_ceiling_ms:.3f}ms)"
    )
    assert elapsed_ms < normalized_ceiling_ms, (
        f"{series_count}개 시계열 export가 정규화 상한({normalized_ceiling_ms:.1f}ms)을 "
        f"초과했습니다(실측 {elapsed_ms:.1f}ms) — 시계열 수 대비 선형이 아닌 비용 회귀 의심"
        "(모듈 docstring 34행 근사치 설계 근거 참고)"
    )


async def test_scheduler_concurrent_run_once_invocations_do_not_corrupt_shared_pool(
    deps,
) -> None:
    """적대적/동시성 증명 — 서로 다른 감시 대상을 가진 두 스케줄러 인스턴스가
    같은 커넥션 풀을 공유한 채 `run_once()`를 동시에(asyncio.gather) 실행해도
    서로의 ingest·게이지 export를 오염시키지 않아야 한다(§4.1 STALE 판정·
    §5 `md_candle` `ON CONFLICT DO NOTHING`이 동시 삽입에도 안전하다는
    전제의 직접 증명)."""
    instrument_a = await _listed_instrument(deps)
    instrument_b = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]

    def _make_scheduler(instrument) -> MarketDataQualityScheduler:
        return MarketDataQualityScheduler(
            deps.pool,
            store=deps.store,
            refs=deps.refs,
            cal=deps.cal,
            batches=deps.batches,
            registry=MetricsRegistry(),
            source=_FakeIngestSource(candles),
            audit=deps.audit,
            watched=[
                WatchedSeries(
                    venue=Venue.BITGET,
                    canonical_symbol=instrument.canonical_symbol,
                    timeframe=Timeframe.M1,
                    lookback=timedelta(minutes=5),
                ),
            ],
            clock=_clock(t0 + timedelta(minutes=1)),
        )

    scheduler_a = _make_scheduler(instrument_a)
    scheduler_b = _make_scheduler(instrument_b)

    report_a, report_b = await asyncio.gather(scheduler_a.run_once(), scheduler_b.run_once())

    assert report_a.ingested == [f"BITGET:{instrument_a.canonical_symbol}:1m"]
    assert report_b.ingested == [f"BITGET:{instrument_b.canonical_symbol}:1m"]
    assert not report_a.ingest_failed
    assert not report_b.ingest_failed

    ids_a = {m.key.instrument_id for m in report_a.metrics}
    ids_b = {m.key.instrument_id for m in report_b.metrics}
    assert instrument_a.instrument_id in ids_a
    assert instrument_b.instrument_id in ids_b

    async with deps.pool.acquire() as conn:
        count_a = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument_a.instrument_id
        )
        count_b = await conn.fetchval(
            "SELECT COUNT(*) FROM md_candle WHERE instrument_id = $1", instrument_b.instrument_id
        )
    assert count_a == 1, "동시 실행이 서로의 candle 저장을 중복·오염시키지 않아야 한다"
    assert count_b == 1, "동시 실행이 서로의 candle 저장을 중복·오염시키지 않아야 한다"
