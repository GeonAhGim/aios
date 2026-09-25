"""LA-18 — Exports recent-batch and staleness state as observability gauges.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.1(STALE forced
at: scheduler), §7(`md_staleness_seconds`, `md_gap_ratio_24h` gauges), §9.2
LA-18.

STALE determination uses `domain/quality/stale_detector.detect_stale`(LA-5)
unchanged — this function is exactly why `ingest_candles`(LA-15) removed
that check from the pipeline (see that module docstring). Session-open
status (§4.1 "session is open") is resolved the same way as
`ingest_candles._sessions_in_range`: crypto (BITGET, continuous) uses a
constant spec without calendar lookup; KRX/US queries
`CalendarRepository`(LA-12).

Deviation 1: Spec §2.2 table lists this module's dependencies (ports) as
"batches, store" only, but STALE determination needs session-open status
(§4.1), so we also accept `cal: CalendarRepository` — not to re-implement
session logic here, which the no-reimplementation rule forbids.

Deviation 2: No port in this leaf scope (LA-9, 5 ports) provides "which
(venue, instrument, timeframe) series to observe" — `ReferenceRepository`
has no list-all method, `BatchRepository` has no recent-batch list.
Following the precedent of
`LedgerIntegrityScheduler._fetch_payout_capture_candidates`, we query
`md_ingest_batch` directly via SQL to select series that had a batch in
the last 24h (cross-cutting lists that don't fit a single port are
handled by the application layer querying `pool` directly — an existing
pattern in this codebase).

`gap_ratio_24h`/`reject_ratio_24h` are not exact 24h accumulations but
are based on each series' **most recent batch** (`QualityVerdict`
reconstructed by `batches.get()`) — attempting cumulative aggregation
across multiple batches would require repeated `batches.get()` calls
(each call does 2 COUNTs on `md_candle`/`md_quarantine_candle` + full
issue scan), making scheduler-cycle cost grow super-linearly as series
count increases. "Most recent single batch" is an approximation, but
since §4.1 GAP/REJECT determinations are themselves batch-scoped,
reflecting this batch's state is the right direction (Draft, §10
unspecified).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

import asyncpg

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.market_data.contracts.v1 import (
    DataQualityMetrics,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore

__all__ = ["Clock", "export_quality_metrics"]

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

_ACTIVITY_WINDOW = timedelta(hours=24)
_GAUGE_LABELS = ("venue", "instrument_id", "timeframe")


async def _session_open(
    conn: asyncpg.Connection, venue: Venue, at: datetime, cal: CalendarRepository
) -> bool:
    spec = KNOWN_SESSIONS[venue.value]
    if spec.continuous:
        return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec).is_open(at)
    calendar = await cal.load(conn, venue, at.astimezone(spec.tz).year)
    return calendar.is_open(at)


async def _active_series(
    conn: asyncpg.Connection, window_start: datetime
) -> list[SeriesKey]:
    """Series that have at least one row in `md_ingest_batch` within the last `_ACTIVITY_WINDOW`."""
    rows = await conn.fetch(
        "SELECT DISTINCT venue, instrument_id, timeframe FROM md_ingest_batch "
        "WHERE created_at >= $1",
        window_start,
    )
    return [
        SeriesKey(
            venue=Venue(row["venue"]),
            instrument_id=row["instrument_id"],
            timeframe=Timeframe(row["timeframe"]),
        )
        for row in rows
    ]


async def _latest_batch(
    conn: asyncpg.Connection, key: SeriesKey, window_start: datetime
) -> tuple[UUID, UUID | None] | None:
    """`(batch_id, tenant_id)` — the scheduler is an internal job that scans
    all tenants, so this query itself is not narrowed by tenant (§4.1 Deviation 2).
    We also return the owning `tenant_id` to pass through to the subsequent
    `batches.get()` call — after LA-22 added tenant filtering to `get()`,
    passing any arbitrary tenant_id would trigger a "not-found-hidden" error
    and prevent reading our own batch."""
    row = await conn.fetchrow(
        "SELECT id, tenant_id FROM md_ingest_batch WHERE venue = $1 AND instrument_id = $2 "
        "AND timeframe = $3 AND created_at >= $4 ORDER BY created_at DESC LIMIT 1",
        key.venue.value,
        key.instrument_id,
        key.timeframe.value,
        window_start,
    )
    if row is None:
        return None
    return cast("UUID", row["id"]), cast("UUID | None", row["tenant_id"])


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


async def _export_one(
    conn: asyncpg.Connection,
    key: SeriesKey,
    window_start: datetime,
    now: datetime,
    *,
    batches: BatchRepository,
    store: CandleStore,
    cal: CalendarRepository,
    registry: MetricsRegistry,
) -> DataQualityMetrics | None:
    last_open_time = await store.last_open_time(conn, key)
    if last_open_time is None:
        logger.warning(
            "quality_metrics: venue=%s instrument_id=%s tf=%s 저장된 캔들 없음 — 이번 주기 스킵",
            key.venue.value,
            key.instrument_id,
            key.timeframe.value,
        )
        return None

    latest = await _latest_batch(conn, key, window_start)
    last_batch_id = latest[0] if latest is not None else None
    gap_count = 0
    reject_count = 0
    record_count = 0
    if latest is not None:
        batch_id, batch_tenant_id = latest
        batch = await batches.get(conn, batch_id, batch_tenant_id)
        if batch is not None:
            verdict = batch.verdict
            record_count = verdict.accepted + verdict.quarantined + verdict.rejected
            # `verdict.rejected`(reconstructed value) is effectively always 0
            # due to current adapter behavior where REJECT candles are also
            # stored in the quarantine table (see postgres_batch_repository.py
            # docstring) — instead, we count REJECT severity directly from the
            # issues list. A single candle can produce multiple issues
            # (e.g., high<open and volume<0 violations simultaneously),
            # so we deduplicate by open_time.
            gap_count = len(
                {i.open_time for i in verdict.issues if i.type is QualityIssueType.GAP}
            )
            reject_count = len(
                {i.open_time for i in verdict.issues if i.severity is Severity.REJECT}
            )

    staleness_s = int((now - last_open_time).total_seconds())
    session_open = await _session_open(conn, key.venue, now, cal)
    stale_issue = detect_stale(last_open_time, now, key.timeframe, session_open)

    gap_ratio = _ratio(gap_count, gap_count + record_count)
    reject_ratio = _ratio(reject_count, record_count)

    labels = {
        "venue": key.venue.value,
        "instrument_id": str(key.instrument_id),
        "timeframe": key.timeframe.value,
    }
    registry.gauge("md_staleness_seconds", _GAUGE_LABELS).set(float(staleness_s), **labels)
    registry.gauge("md_gap_ratio_24h", _GAUGE_LABELS).set(float(gap_ratio), **labels)
    registry.gauge("md_reject_ratio_24h", _GAUGE_LABELS).set(float(reject_ratio), **labels)
    if stale_issue is not None:
        registry.counter("md_quality_issues_total", ("type", "severity")).inc(
            type=QualityIssueType.STALE.value, severity=Severity.WARN.value
        )

    return DataQualityMetrics(
        key=key,
        staleness_s=staleness_s,
        gap_ratio_24h=gap_ratio,
        reject_ratio_24h=reject_ratio,
        last_batch_id=last_batch_id,
    )


async def export_quality_metrics(
    *,
    batches: BatchRepository,
    store: CandleStore,
    cal: CalendarRepository,
    pool: asyncpg.Pool,
    registry: MetricsRegistry,
    clock: Clock,
) -> list[DataQualityMetrics]:
    """Updates staleness/gap/reject-ratio gauges for each series with activity
    in the last 24 hours.

    A computation failure (exception) for one series is logged and skipped —
    remaining series continue processing (§9 LA-18 DoD: "failure on one
    symbol must not block the rest")."""
    now = clock()
    window_start = now - _ACTIVITY_WINDOW

    results: list[DataQualityMetrics] = []
    async with pool.acquire() as conn:
        for key in await _active_series(conn, window_start):
            try:
                metrics = await _export_one(
                    conn, key, window_start, now,
                    batches=batches, store=store, cal=cal, registry=registry,
                )
            except Exception:
                logger.exception(
                    "quality_metrics: venue=%s instrument_id=%s tf=%s 지표 계산 실패 — "
                    "나머지 시계열은 계속 처리",
                    key.venue.value,
                    key.instrument_id,
                    key.timeframe.value,
                )
                continue
            if metrics is not None:
                results.append(metrics)
    return results
