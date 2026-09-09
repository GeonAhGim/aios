"""DC-16 — market-data backfill job: plan gaps -> fetch from source -> store -> refresh coverage.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16 (prerequisites DC-7=task-1178, DC-11=task-1187, DC-13=task-1212, all done).

This module only orchestrates three delegated responsibilities (§C forbids
duplicating context — do not reimplement the algorithms): gap computation is
`domain/coverage/gaps.plan_fetch` (DC-7), source fetch is
`ports/provider.MarketDataProvider` (DC-5/11 SPI — this file does not know
about concrete vendor adapters), storage is `ports/candle_store.
CandleStore` (LA-9 port — already implemented by
`adapters/storage/hot_postgres.HotPostgresStorage` in DC-13), and coverage
merge is delegated to `domain/coverage/registry.merge_spans` (DC-6).

Boundary 1 — `CoverageSpan` dual contract (left as-is, matching the deviation
already documented by `adapters/postgres_coverage_repository.py` and
`application/get_coverage.py`): the storage port
(`ports/coverage_repository.CoverageSpan` — 2-tier quality, no asset_class)
and the merge domain (`contracts/v2/coverage.CoverageSpan` — 3-tier
quality_grade, asset_class required) coexist in the codebase. This module
round-trips between them using the same mapping `get_coverage.py`
(task-2195) already wrote (that mapping is a private constant, so it is not
re-imported — the same content is duplicated in this file instead).

Boundary 2 — `instrument_id` namespace (already documented by
hot_postgres.py): `CandleStore`/`SeriesKey` use the `md_instrument` (UUID)
key, while `CoverageSpan`/`VenueListing` use the DC-1 `instruments` (ULID)
key. Mapping between the two is out of scope for this leaf (same decision as
hot_postgres.py) — the caller is assumed to already know both, so this
function takes `series_key` (md_instrument axis) and `listing` (ULID axis —
`listing.instrument_id` is the coverage query key) separately. If their
`venue` values differ, the request has conflated two different series, so it
is rejected fail-closed.

Resume after interruption — this function does not open its own transaction
(LA-9 port docstring: transaction boundaries are the caller's
responsibility). Each gap runs `store.upsert_batch` then
`coverage_repo.upsert_span` in order, and unless the caller wraps the call in
an explicit transaction, each SQL statement commits immediately — if gap N
raises, gaps 1..N-1 are already persisted. Calling this function again makes
`list_spans`/`query` read the latest DB state, so `plan_fetch` will not
re-flag ranges that are already filled as gaps — that alone is the resume
mechanism; no separate checkpoint table is needed.

No silent zero-fill (§4.1) — if the provider returns zero candles for a gap
(a genuine absence of data, not an error), that gap is left with
`stored=0`/`span=None` and no coverage span is created. The next run's
`plan_fetch` will report the same range as a gap again, so "empty response =
covered" is never silently assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan as MergeCoverageSpan
from src.foundation.market_data.contracts.v2.coverage import QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns, to_candle_records
from src.foundation.market_data.domain.coverage.gaps import CoverageGap, plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.domain.timeframe import duration
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.coverage_repository import CoverageQuality, CoverageRepository
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["BackfillSegmentResult", "BackfillJobResult", "VenueMismatchError", "run_backfill_job"]

_QUALITY_TO_GRADE = {
    CoverageQuality.PROVISIONAL: QualityGrade.RAW,
    CoverageQuality.VALIDATED: QualityGrade.VALIDATED,
}


class VenueMismatchError(ValueError):
    """`listing.venue` differs from `series_key.venue` — the two axes disagree,
    so the request is rejected fail-closed rather than treated as one series."""


def _to_merge_span(span: StoredCoverageSpan, *, asset_class: AssetClass) -> MergeCoverageSpan:
    return MergeCoverageSpan(
        instrument_id=span.instrument_id,
        venue=span.venue,
        asset_class=asset_class,
        timeframe=span.timeframe,
        quality_grade=_QUALITY_TO_GRADE[span.quality],
        start_at=span.start,
        end_at=span.end,
    )


def _validated_candles(columns: CandleColumns, key: SeriesKey) -> list[CandleRecord]:
    """Convert the column array the provider just returned (not yet passed
    through `ohlc_sanity.check_candle`) into `CandleRecord`s. Reuse
    `to_candle_records`'s length guard and `close_time` derivation as-is (do
    not reimplement), but the instances it returns skip validation via
    `model_construct`, so they are not safe for unvalidated source data
    before storage (that function's docstring: "safe only for data that has
    passed check_candle at write time") — so each record is passed back
    through the pydantic constructor to validate type/format. Semantic
    quality gates such as OHLC sign/spike checks are out of scope for this
    leaf (handled by LA-15 `ingest_candles` on the live path)."""
    unchecked = to_candle_records(columns, key)
    return [CandleRecord.model_validate(c.model_dump()) for c in unchecked]


@dataclass(frozen=True, slots=True)
class BackfillSegmentResult:
    """Result of processing one gap. `stored=0` means the provider reported
    no candles for that range (not an error) — in that case `span` is
    `None` and no coverage span is created either (§4.1 no silent zero-fill)."""

    gap: CoverageGap
    stored: int
    span: StoredCoverageSpan | None


@dataclass(frozen=True, slots=True)
class BackfillJobResult:
    gaps_planned: int
    segments: list[BackfillSegmentResult]
    merged_coverage: list[MergeCoverageSpan]


async def run_backfill_job(
    *,
    conn: asyncpg.Connection,
    provider: MarketDataProvider,
    store: CandleStore,
    coverage_repo: CoverageRepository,
    listing: VenueListing,
    series_key: SeriesKey,
    tf: Timeframe,
    calendar: VenueCalendar,
    asset_class: AssetClass,
    quality: CoverageQuality,
    range_start: datetime,
    range_end: datetime,
) -> BackfillJobResult:
    """For each gap `plan_fetch` (DC-7) finds in `[range_start, range_end)`,
    fill it via `provider.fetch_candles`, store it via `store.upsert_batch`,
    then refresh coverage via `coverage_repo.upsert_span`. `plan_fetch`
    already returns gaps sorted with no overlap, so processing them in order
    is sufficient (iterate the single `plan_fetch` result as-is, no
    re-planning)."""
    if listing.venue is not series_key.venue:
        raise VenueMismatchError(
            f"listing.venue({listing.venue.value}) != series_key.venue"
            f"({series_key.venue.value})"
        )

    stored_spans = [
        span
        for span in await coverage_repo.list_spans(conn, listing.instrument_id, tf)
        if span.venue is listing.venue
    ]
    existing_candles = await store.query(conn, series_key, range_start, range_end, as_of=None)

    gaps = plan_fetch(
        spans=[_to_merge_span(s, asset_class=asset_class) for s in stored_spans],
        candles=existing_candles,
        tf=tf,
        calendar=calendar,
        range_start=range_start,
        range_end=range_end,
    )

    merged = [_to_merge_span(s, asset_class=asset_class) for s in stored_spans]
    segments: list[BackfillSegmentResult] = []
    for gap in gaps:
        columns = await provider.fetch_candles(
            listing, tf, TimeSpan(start=gap.start_at, end=gap.end_at)
        )
        candles = _validated_candles(columns, series_key)
        if not candles:
            segments.append(BackfillSegmentResult(gap=gap, stored=0, span=None))
            continue

        stored_count = await store.upsert_batch(conn, uuid4(), candles)
        new_span = StoredCoverageSpan(
            instrument_id=listing.instrument_id,
            venue=listing.venue,
            timeframe=tf,
            quality=quality,
            start=candles[0].open_time,
            end=candles[-1].open_time + duration(tf),
        )
        saved_span = await coverage_repo.upsert_span(conn, new_span)
        merged = merge_spans([*merged, _to_merge_span(saved_span, asset_class=asset_class)])
        segments.append(BackfillSegmentResult(gap=gap, stored=stored_count, span=saved_span))

    return BackfillJobResult(gaps_planned=len(gaps), segments=segments, merged_coverage=merged)
