"""LA-18 quality_metrics/scheduler 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-18.
DoD(task-712): 스케줄러 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 차단
안 함.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.market_data.application.quality_metrics import export_quality_metrics
from src.foundation.market_data.application.scheduler import (
    MarketDataQualityScheduler,
    WatchedSeries,
)
from src.foundation.market_data.contracts.v1 import (
    Timeframe,
    Venue,
)
from tests.foundation.integration.market_data.quality_metrics_helpers import (
    _candle,
    _clock,
    _FakeIngestSource,
    _FlakyCandleStore,
    _ingest,
    _listed_instrument,
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


