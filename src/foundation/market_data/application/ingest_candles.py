"""LA-15 — Candle ingest use case: fetch → quality gate → store/quarantine → audit.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.1, §8.2, §9.2 LA-15.

The pipeline delegates to existing pure domain modules (LA-4~8) only (no reimplementation):
`dedupe`(LA-4) → `check_candle`(LA-4) → `detect_spikes`(LA-6) → `detect_gaps`
(LA-5) → `decide`(LA-6). STALE (LA-5 `detect_stale`) is not placed in this pipeline —
per §4.1 table "forced placement: scheduler", it is the responsibility of LA-18
`quality_metrics` to periodically scan stored batches and make that determination
(re-running it on every ingest would cause backfill-like requests with past `range_end`
to always carry a STALE WARN, making the verdict unnecessarily PARTIAL).

`IngestSource.fetch_candles`(LA-9 port, already fixed) does not know about the DB, so
the `CandleRecord.key.instrument_id` it returns is meaningless (an arbitrary value chosen
by the adapter) — this function must unconditionally rewrite the key with the real
`instrument_id` looked up from reference data (this function does not care which placeholder
the adapter chose).

Transaction boundary: reference data · calendar lookup (read-only) and source fetch (external
HTTP) are performed outside the DB transaction — to avoid holding a connection during slow
external I/O (LA-14 was all DB I/O so this distinction did not apply). Only store · batch
record · audit event are grouped into a single transaction — if anything fails inside this
final block (especially `audit.append_event_in` failure injection), `md_candle` ·
`md_quarantine_candle` · `md_ingest_batch` · `md_quality_issue` are all rolled back
(§9 LA-15 DoD).

§4.1 "batch REJECT ratio > 20% → entire batch QUARANTINE (no partial store)" is already
decided by `verdict.decide`, so when the result is QUARANTINE, the entire original
(`rekeyed`) set is quarantined, including the "good" candles selected for storage.

Known constraint: `PostgresReferenceRepository.register()`(LA-12) initially writes the alias
in venue raw symbol format (`cmd.venue_symbol`), while the RENAME path of
`apply_lifecycle_event`(LA-14) writes the new alias in canonical format — formats are mixed
in the same `md_symbol_alias` table (KRX/US do not show it because canonical==venue raw,
and only BITGET with BASE/QUOTE slash differs). This function assumes instruments not yet
renamed and looks up `cmd.canonical_symbol` converted to venue raw format via
`symbol_normalizer.to_venue` — lookups after RENAME are outside this leaf's scope
(LA-12/LA-14 alias format consistency must be addressed first).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg

from src.foundation.evidence.api import (
    AuditEvent,
    Classification,
    Outcome,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    IngestCandlesCommand,
    QualityIssue,
    SeriesKey,
    SessionWindow,
    Severity,
    SymbolStatus,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.lineage import batch_hash, request_fingerprint
from src.foundation.market_data.domain.quality.dedupe import dedupe
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.quality.ohlc_sanity import check_candle
from src.foundation.market_data.domain.quality.outlier_detector import detect_spikes
from src.foundation.market_data.domain.quality.verdict import decide
from src.foundation.market_data.domain.reference.symbol_normalizer import to_venue
from src.foundation.market_data.ports.batch_repository import BatchRepository
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.ingest_source import IngestSource
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AuditAppender",
    "Clock",
    "SymbolNotTradableError",
    "SymbolUnknownError",
    "ingest_candles",
]

Clock = Callable[[], datetime]

_NOT_TRADABLE = frozenset({SymbolStatus.SUSPENDED, SymbolStatus.DELISTED})


class SymbolUnknownError(Exception):
    """`MD_SYMBOL_UNKNOWN` — `(venue, canonical_symbol)`이 참조데이터에 없음."""


class SymbolNotTradableError(Exception):
    """`MD_SYMBOL_NOT_TRADABLE` — Symbol in SUSPENDED/DELISTED status rejected for ingest."""


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


def _bitget_calendar() -> VenueCalendar:
    session = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=session.tz, regular=session)


async def _sessions_in_range(
    conn: asyncpg.Connection,
    venue: Venue,
    start: datetime,
    end: datetime,
    cal: CalendarRepository,
) -> list[SessionWindow]:
    """Return only sessions overlapping `[start, end)`, clipped to that range —
    since `gap_detector.detect_gaps` reconstructs the expected range from each
    session's `min(open_at)/max(close_at)`, passing sessions that extend beyond
    the requested range would create false GAPs at times we never fetched."""
    tz = KNOWN_SESSIONS[venue.value].tz
    day = start.astimezone(tz).date()
    last_day = end.astimezone(tz).date()
    calendars: dict[int, VenueCalendar] = {}
    windows: list[SessionWindow] = []
    one_day = timedelta(days=1)
    while day <= last_day:
        calendar = calendars.get(day.year)
        if calendar is None:
            calendar = _bitget_calendar() if venue is Venue.BITGET else await cal.load(
                conn, venue, day.year
            )
            calendars[day.year] = calendar
        for window in calendar.sessions_for(day):
            clipped_open = max(window.open_at, start)
            clipped_close = min(window.close_at, end)
            if clipped_open < clipped_close:
                windows.append(
                    SessionWindow(open_at=clipped_open, close_at=clipped_close, kind=window.kind)
                )
        day += one_day
    return windows


def _sort_by_open_time(candles: list[CandleRecord]) -> list[CandleRecord]:
    return sorted(candles, key=lambda c: c.open_time)


async def ingest_candles(
    cmd: IngestCandlesCommand,
    *,
    source: IngestSource,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    batches: BatchRepository,
    audit: AuditAppender,
    pool: asyncpg.Pool,
    clock: Clock,
) -> IngestBatchResult:
    now = clock()
    lookup_symbol = to_venue(cmd.venue, cmd.canonical_symbol)

    async with pool.acquire() as read_conn:
        instrument = await refs.get_instrument(read_conn, cmd.venue, lookup_symbol, now)
        if instrument is None:
            raise SymbolUnknownError(
                f"참조데이터 없음: venue={cmd.venue.value} canonical={cmd.canonical_symbol!r}"
            )
        if instrument.status in _NOT_TRADABLE:
            raise SymbolNotTradableError(
                f"ingest 불가 상태: instrument_id={instrument.instrument_id} "
                f"status={instrument.status.value}"
            )
        sessions = await _sessions_in_range(
            read_conn, cmd.venue, cmd.range_start, cmd.range_end, cal
        )

    raw_candles = await source.fetch_candles(
        cmd.venue, instrument.venue_symbol, cmd.timeframe, cmd.range_start, cmd.range_end
    )
    key = SeriesKey(
        venue=cmd.venue, instrument_id=instrument.instrument_id, timeframe=cmd.timeframe
    )
    rekeyed = [c.model_copy(update={"key": key}) for c in raw_candles]

    dedupe_result = dedupe(rekeyed)
    sanity_issues: list[QualityIssue] = []
    good: list[CandleRecord] = []
    bad: list[CandleRecord] = []
    for candle in dedupe_result.kept:
        issues = check_candle(candle)
        sanity_issues.extend(issues)
        if any(issue.severity is Severity.REJECT for issue in issues):
            bad.append(candle)
        else:
            good.append(candle)
    good = _sort_by_open_time(good)

    spike_issues = detect_spikes(good)
    gap_issues = detect_gaps(list(dedupe_result.kept), cmd.timeframe, sessions)
    all_issues = [*dedupe_result.issues, *sanity_issues, *spike_issues, *gap_issues]
    verdict_result = decide(all_issues, len(rekeyed))

    batch_id = uuid4()
    is_stored = verdict_result.verdict in (Verdict.ACCEPT, Verdict.PARTIAL)
    stored_range = (good[0].open_time, good[-1].open_time) if is_stored and good else None
    quarantine_candles = (
        [*bad, *dedupe_result.conflicts] if is_stored else (rekeyed if rekeyed else [])
    )

    async with pool.acquire() as conn, conn.transaction():
        # `md_candle`/`md_quarantine_candle`.batch_id is FK to `md_ingest_batch(id)` —
        # the batch row (and its required audit_event_id) must be committed first
        # before candles can be written. Since everything is in one transaction,
        # any failure at any stage rolls back together (§9 LA-15 DoD).
        outcome = Outcome.SUCCESS if is_stored else Outcome.DENIED
        payload: dict[str, object] = {
            "batch_id": str(batch_id),
            "venue": cmd.venue.value,
            "canonical_symbol": cmd.canonical_symbol,
            "timeframe": cmd.timeframe.value,
            "range_start": cmd.range_start.isoformat(),
            "range_end": cmd.range_end.isoformat(),
            "verdict": verdict_result.verdict.value,
            "accepted": verdict_result.accepted,
            "quarantined": verdict_result.quarantined,
            "rejected": verdict_result.rejected,
        }
        assert_safe_payload(payload)
        event = await audit.append_event_in(
            conn,
            tenant_id=cmd.tenant_id,
            aggregate_type="md_ingest_batch",
            aggregate_id=batch_id,
            aggregate_revision=None,
            action="market_data.candles_ingested",
            outcome=outcome,
            actor_subject_id=None,
            trace_id=cmd.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )

        batch_result = IngestBatchResult(
            batch_id=batch_id,
            tenant_id=cmd.tenant_id,
            source=cmd.venue.value,
            venue=cmd.venue,
            instrument_id=instrument.instrument_id,
            timeframe=cmd.timeframe,
            range_start=cmd.range_start,
            range_end=cmd.range_end,
            request_fingerprint=request_fingerprint(
                cmd.venue.value,
                {
                    "canonical_symbol": cmd.canonical_symbol,
                    "timeframe": cmd.timeframe.value,
                    "range_start": cmd.range_start.isoformat(),
                    "range_end": cmd.range_end.isoformat(),
                },
            ),
            verdict=verdict_result,
            batch_hash=batch_hash(rekeyed),
            audit_event_id=event.id,
            stored_range=stored_range,
        )
        created = await batches.create(conn, batch_result)

        if is_stored:
            await store.upsert_batch(conn, batch_id, good)
        if quarantine_candles:
            await store.quarantine(conn, batch_id, quarantine_candles, all_issues)
        await batches.add_issues(conn, batch_id, all_issues)

    return created
