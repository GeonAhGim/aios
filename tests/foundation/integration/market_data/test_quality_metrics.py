"""LA-18 quality_metrics/scheduler 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-18.
DoD(task-712): 스케줄러 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 차단
안 함.
"""
from __future__ import annotations

import os
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
        open_time=t, close_time=t + timedelta(minutes=1),
        open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(c), volume=Decimal(v),
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
        venue=Venue.BITGET, venue_symbol=_bitget_symbol(), asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"), lot_size=Decimal("0.0001"), listed_at=listed_at,
        actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(),
    )
    instrument = await register_instrument(deps.pool, cmd, refs=deps.refs, audit=deps.audit)
    return await apply_lifecycle_event(
        deps.pool,
        LifecycleEventCommand(
            instrument_id=instrument.instrument_id, event="LIST",
            effective_at=datetime.now(timezone.utc), source_ref="test:list",
            actor_subject_id=uuid.uuid4(), trace_id=uuid.uuid4(),
        ),
        current=instrument, refs=deps.refs, audit=deps.audit,
    )


async def _ingest(deps, instrument, candles, *, start, end, at):
    from src.foundation.market_data.application.ingest_candles import ingest_candles

    cmd = IngestCandlesCommand(
        tenant_id=None, venue=Venue.BITGET, canonical_symbol=instrument.canonical_symbol,
        timeframe=Timeframe.M1, range_start=start, range_end=end, trace_id=uuid.uuid4(),
    )
    return await ingest_candles(
        cmd, source=_FakeIngestSource(candles), store=deps.store, refs=deps.refs, cal=deps.cal,
        batches=deps.batches, audit=deps.audit, pool=deps.pool, clock=_clock(at),
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
        batches=deps.batches, store=deps.store, cal=deps.cal, pool=deps.pool,
        registry=registry, clock=_clock(later),
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
        await _ingest(
            deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=1), at=t0
        )

    flaky_store = _FlakyCandleStore(deps.store, boom_instrument.instrument_id)
    later = t0 + timedelta(minutes=5)
    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches, store=flaky_store, cal=deps.cal, pool=deps.pool,
        registry=registry, clock=_clock(later),
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
        good(t0), good(t0 + timedelta(minutes=1)), good(t0 + timedelta(minutes=2)),
        good(t0 + timedelta(minutes=3)), bad, good(t0 + timedelta(minutes=6)),
    ]  # minute 5 빠짐 → GAP 1건, minute 4는 REJECT 1건 — 총 6건 중 1건 rejected(<20%)
    await _ingest(deps, instrument, candles, start=t0, end=t0 + timedelta(minutes=7), at=t0)

    registry = MetricsRegistry()
    results = await export_quality_metrics(
        batches=deps.batches, store=deps.store, cal=deps.cal, pool=deps.pool,
        registry=registry, clock=_clock(t0 + timedelta(minutes=10)),
    )

    mine = next(m for m in results if m.key.instrument_id == instrument.instrument_id)
    assert mine.gap_ratio_24h == Decimal(1) / Decimal(7)
    assert mine.reject_ratio_24h == Decimal(1) / Decimal(6)


async def test_scheduler_run_once_isolates_ingest_failure_and_exports_metrics(deps):
    instrument = await _listed_instrument(deps)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candles = [_candle(t0, "100", "110", "90", "105", "10")]

    scheduler = MarketDataQualityScheduler(
        deps.pool, store=deps.store, refs=deps.refs, cal=deps.cal, batches=deps.batches,
        registry=MetricsRegistry(), source=_FakeIngestSource(candles), audit=deps.audit,
        watched=[
            WatchedSeries(
                venue=Venue.BITGET, canonical_symbol=instrument.canonical_symbol,
                timeframe=Timeframe.M1, lookback=timedelta(minutes=5),
            ),
            WatchedSeries(
                venue=Venue.BITGET, canonical_symbol="UNKNOWN-NOT-REGISTERED",
                timeframe=Timeframe.M1, lookback=timedelta(minutes=5),
            ),
        ],
        clock=_clock(t0 + timedelta(minutes=1)),
    )

    report = await scheduler.run_once()

    assert len(report.ingested) == 1
    assert len(report.ingest_failed) == 1, "등록되지 않은 심볼 하나는 실패해야 한다"
    metric_ids = {m.key.instrument_id for m in report.metrics}
    assert instrument.instrument_id in metric_ids, "실패한 대상이 게이지 export를 막지 않는다"
