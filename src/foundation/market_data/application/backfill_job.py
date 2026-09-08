"""DC-16 — gap plan -> backfill -> coverage update, resumable orchestrator.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16 (preceded by DC-7 (task-1178), DC-11 (task-1187), DC-13 (task-1212),
all done).

This module is a thin orchestrator only. It never computes gaps itself
(§C "no duplicated context") — gap planning is delegated wholesale to
`domain/coverage/gaps.plan_fetch` (DC-7), which already re-uses LA-5's
`detect_gaps` and DC-6's `merge_spans`. Sources are received only through
the vendor-neutral SPI `Protocol` (`ports/provider.MarketDataProvider`,
DC-5/DC-11) — no concrete vendor adapter (`adapters/providers/bitget_provider.py`,
`kis_provider.py`) is imported here. Storage is delegated to the
`CandleStore` port (DC-13's `adapters/storage/hot_postgres.py` implements
it via `PostgresCandleStore`) — this leaf issues no SQL of its own. After
each gap segment is stored, the declared coverage is updated by folding the
newly-covered span into the caller-supplied set with `domain/coverage/
registry.merge_spans` (DC-6) and handing the merged span to a small
`CoverageSpanStore` port (persistence itself is out of this leaf's scope,
same spirit as `AuditAppender` in `ingest_candles.py`: this file only
declares the minimal shape it needs).

Resumability: gaps are planned once up front from the coverage state at
call time, then fetched/stored one at a time in order. If `provider.
fetch_candles` raises on gap *k*, the spans for gaps `1..k-1` have already
been persisted via `coverage.record_span` and the exception is left to
propagate unchanged (never swallowed into a "successful" return) — the
caller re-invokes this function later, and because `coverage.list_spans()`
now reflects the earlier partial progress, `plan_fetch` recomputes a
shorter gap list that only covers the remaining work (§9 DC-16 DoD b).
`IndeterminateCoverageError` (e.g. a venue absent from the calendar, DC-7
fail-closed) is likewise never caught here — turning it into an empty plan
would silently claim "nothing to do" (§9 DC-16 DoD c).

Known boundary (left honest, not solved here): `ports/candle_store.
CandleStore` keys candles by the LA-namespace `SeriesKey.instrument_id`
(`UUID`, `md_candle`), while `contracts/v2/coverage.CoverageSpan` keys
coverage by the DC-namespace ULID `instrument_id` (`coverage_spans`,
`hot_postgres.py` flags this exact gap as "needs_decision" for DC-16).
This leaf does not bridge the two identifier spaces — callers pass both
explicitly (`series_key` for storage, `listing.instrument_id` for
coverage) and no mapping logic is invented here.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import uuid4

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.coverage.gaps import plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["CoverageSpanStore", "run_backfill"]


class CoverageSpanStore(Protocol):
    """DC-16's minimal coverage-persistence need — read the currently
    declared spans for the single (instrument, venue, tf, quality_grade)
    axis this call targets, and record one newly-covered span at a time.

    No new table/migration is introduced by this leaf (§9 DC-16 DoD e) —
    persisting the merged result is the concrete implementation's concern
    (out of scope here, same as `AuditAppender` in `ingest_candles.py`);
    tests inject an in-memory fake.
    """

    async def list_spans(self) -> list[CoverageSpan]: ...

    async def record_span(self, span: CoverageSpan) -> None: ...


async def run_backfill(
    *,
    conn: asyncpg.Connection,
    listing: VenueListing,
    series_key: SeriesKey,
    tf: Timeframe,
    asset_class: AssetClass,
    quality_grade: QualityGrade,
    calendar: VenueCalendar,
    range_start: datetime,
    range_end: datetime,
    known_candles: Sequence[CandleRecord] = (),
    coverage: CoverageSpanStore,
    provider: MarketDataProvider,
    store: CandleStore,
) -> list[CoverageSpan]:
    """Plan the gaps in `[range_start, range_end)`, fetch+store each one in
    order, and fold each newly-stored span into the declared coverage.

    Returns the merged coverage spans reflecting everything stored by this
    call (on top of whatever `coverage.list_spans()` already reported).
    If a gap fails to fetch, everything stored before it stays recorded and
    the exception propagates — nothing here catches it (see module
    docstring: resumability + fail-closed rejection both depend on that).
    """
    spans = await coverage.list_spans()
    gaps = plan_fetch(
        spans=spans,
        candles=known_candles,
        tf=tf,
        calendar=calendar,
        range_start=range_start,
        range_end=range_end,
    )

    current = merge_spans(spans)
    for gap in gaps:
        columns = await provider.fetch_candles(
            listing, tf, TimeSpan(start=gap.start_at, end=gap.end_at)
        )
        records = to_candle_records(columns, series_key)
        await store.upsert_batch(conn, uuid4(), records)

        new_span = CoverageSpan(
            instrument_id=listing.instrument_id,
            venue=listing.venue,
            asset_class=asset_class,
            timeframe=tf,
            quality_grade=quality_grade,
            start_at=gap.start_at,
            end_at=gap.end_at,
        )
        await coverage.record_span(new_span)
        current = merge_spans([*current, new_span])

    return current
