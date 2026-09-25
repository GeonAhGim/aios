"""LA-17 — Candle retrieval. Queries batches saved before `as_of`, with
RAW|ADJUSTED selection.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-17.

This module does not reimplement logic — batch hashes delegate to
`domain/lineage.batch_hash` (LA-8), adjustment factors to
`domain/corporate_actions/adjustment` (LA-8), and gap detection to
`domain/quality/gap_detector.detect_gaps` (LA-5) + `VenueCalendar` (LA-3).
Candle data reads go only through `ports/candle_store.CandleStore` (LA-13
adapter); no new SQL is written in this leaf.

**Unverified / Constraint**: `ReferenceRepository` (LA-9/12) has no lookup
that confirms instrument existence by `instrument_id` alone (the only one
is `get_instrument`, which searches by canonical symbol). Therefore
"unregistered instrument" is inferred from
`CandleStore.last_open_time` being `None` — an approximation that cannot
distinguish "never registered" from "registered but never collected". Both
cases require the same caller action (check reference data, run collection
first), so this is sufficient as a fail-closed signal.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncpg

from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    CandleRecord,
    CandleSeries,
    QualityIssue,
    SeriesKey,
    SessionWindow,
    Venue,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import SessionSpec, VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.corporate_actions.adjustment import adjust, factor_chain
from src.foundation.market_data.domain.lineage import batch_hash
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import duration, expected_opens
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AsOfInFutureError",
    "QuarantinedViewUnsupportedError",
    "UnknownSeriesError",
    "coalesce_gaps",
    "ensure_as_of_not_future",
    "get_candles",
    "load_series",
]


class AsOfInFutureError(ValueError):
    """`MD_AS_OF_IN_FUTURE` — `as_of` is in the future relative to now
    (impossible, request must be corrected)."""

    def __init__(self, as_of: datetime, now: datetime) -> None:
        super().__init__(f"as_of={as_of.isoformat()}가 현재({now.isoformat()})보다 미래입니다.")


class UnknownSeriesError(Exception):
    """`MD_SYMBOL_UNKNOWN` — No candles have ever been stored for this
    (venue, instrument, timeframe) (see approximation constraint in module
    docstring)."""

    def __init__(self, key: SeriesKey) -> None:
        super().__init__(
            f"등록되지 않았거나 수집된 적 없는 시계열: venue={key.venue.value} "
            f"instrument_id={key.instrument_id} timeframe={key.timeframe.value}"
        )
        self.key = key


class QuarantinedViewUnsupportedError(Exception):
    """`CandleQuery.include_quarantined=True` is not supported by
    `CandleStore.query` (LA-13) — we reject it explicitly rather than
    silently ignoring (fail-closed)."""


async def _ensure_known_series(
    conn: asyncpg.Connection, store: CandleStore, key: SeriesKey
) -> None:
    if await store.last_open_time(conn, key) is None:
        raise UnknownSeriesError(key)


def ensure_as_of_not_future(as_of: datetime, now: datetime) -> None:
    if as_of > now:
        raise AsOfInFutureError(as_of, now)


def _effective_as_of(as_of: datetime | None, now: datetime) -> datetime:
    if as_of is None:
        return now
    ensure_as_of_not_future(as_of, now)
    return as_of


async def _sessions_for_range(
    conn: asyncpg.Connection,
    cal: CalendarRepository,
    venue: Venue,
    start: datetime,
    end: datetime,
) -> list[SessionWindow]:
    spec: SessionSpec = KNOWN_SESSIONS[venue.value]
    if spec.continuous:
        calendar = VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)
        return _collect_sessions(calendar, start, end, spec)

    calendars_by_year: dict[int, VenueCalendar] = {}
    sessions: list[SessionWindow] = []
    day = start.astimezone(spec.tz).date()
    end_day = end.astimezone(spec.tz).date()
    while day <= end_day:
        if day.year not in calendars_by_year:
            calendars_by_year[day.year] = await cal.load(conn, venue, day.year)
        sessions.extend(calendars_by_year[day.year].sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _collect_sessions(
    calendar: VenueCalendar, start: datetime, end: datetime, spec: SessionSpec
) -> list[SessionWindow]:
    sessions: list[SessionWindow] = []
    day: date = start.astimezone(spec.tz).date()
    end_day = end.astimezone(spec.tz).date()
    while day <= end_day:
        sessions.extend(calendar.sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _clip_sessions(
    sessions: list[SessionWindow], start: datetime, end: datetime
) -> list[SessionWindow]:
    """`detect_gaps` (LA-5) treats the full set of passed sessions
    (`min(open_at)~max(close_at)`) as the expected range — passing a full
    day's sessions as-is would cause gap detection to span much wider than
    the requested `[start, end)`. So we clip sessions to their intersection
    with the request range (the `detect_gaps` rule that "outside sessions are
    not gaps" remains unchanged)."""
    clipped: list[SessionWindow] = []
    for session in sessions:
        clipped_open = max(session.open_at, start)
        clipped_close = min(session.close_at, end)
        if clipped_open < clipped_close:
            clipped.append(
                SessionWindow(open_at=clipped_open, close_at=clipped_close, kind=session.kind)
            )
    return clipped


def coalesce_gaps(
    missing_opens: list[datetime], step: timedelta
) -> list[tuple[datetime, datetime]]:
    """Groups consecutive (exactly `step` apart) missing open_times into
    `[start, end)` ranges. `CandleSeries.gaps` is a list of ranges, not
    individual timestamps."""
    if not missing_opens:
        return []
    ordered = sorted(missing_opens)
    ranges: list[tuple[datetime, datetime]] = []
    run_start = ordered[0]
    prev = ordered[0]
    for ot in ordered[1:]:
        if ot - prev == step:
            prev = ot
            continue
        ranges.append((run_start, prev + step))
        run_start = ot
        prev = ot
    ranges.append((run_start, prev + step))
    return ranges


async def load_series(
    q: CandleQuery,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    conn: asyncpg.Connection,
    now: datetime,
) -> tuple[list[CandleRecord], list[QualityIssue], int]:
    """Shared core for retrieval → (optional) adjustment → gap detection.
    `get_candles` and `replay_candles` (leaves like LA-17) share logic
    through this single function — only the caller differs in strictness
    (whether replay raises exceptions).

    Returns: (candles, gap issue list, total expected open_time count)."""
    if q.include_quarantined:
        raise QuarantinedViewUnsupportedError()

    key = q.key
    await _ensure_known_series(conn, store, key)

    # LA-23b (ADR-2026-09-04-A #1): bulk consumers (replay + gap detection
    # in this function) read via the columnar path instead of per-record
    # pydantic validation in `query()` — `to_candle_records` reconstructs
    # with `model_construct`, so result values are identical to `query()`
    # (ohlc_sanity already enforced the invariant at write time).
    columns = await store.read_candles_columnar(conn, key, q.start, q.end, q.as_of)
    candles = to_candle_records(columns, key)

    if q.adjustment is Adjustment.ADJUSTED:
        as_of_for_factors = q.as_of if q.as_of is not None else now
        actions = await refs.list_actions(conn, key.instrument_id)
        factors = factor_chain(actions, as_of_for_factors)
        candles = adjust(candles, factors)

    raw_sessions = await _sessions_for_range(conn, cal, key.venue, q.start, q.end)
    sessions = _clip_sessions(raw_sessions, q.start, q.end)
    issues = detect_gaps(candles, key.timeframe, sessions)
    expected_total = len(expected_opens(q.start, q.end, key.timeframe, sessions))
    return candles, issues, expected_total


async def get_candles(
    q: CandleQuery,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    pool: asyncpg.Pool,
) -> CandleSeries:
    """§9.2 LA-17: Query batches saved before `as_of`, RAW|ADJUSTED per
    `adjustment`. Gaps are returned as information only (strict exceptions
    are the responsibility of `replay_candles`)."""
    now = datetime.now(timezone.utc)
    effective_as_of = _effective_as_of(q.as_of, now)

    async with pool.acquire() as conn:
        candles, issues, _expected_total = await load_series(
            q, store=store, refs=refs, cal=cal, conn=conn, now=now
        )

    step = duration(q.key.timeframe)
    gaps = coalesce_gaps([i.open_time for i in issues if i.open_time is not None], step)

    return CandleSeries(
        key=q.key,
        candles=candles,
        gaps=gaps,
        adjustment=q.adjustment,
        as_of=effective_as_of,
        series_hash=batch_hash(candles),
    )
