"""DC-16 — coverage-driven candle backfill.

The job deliberately contains no gap arithmetic.  `plan_fetch` is the single
authority for deciding which half-open intervals need fetching; this module
only coordinates the provider, candle store, and coverage registry.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns, to_candle_records
from src.foundation.market_data.domain.coverage.gaps import plan_fetch
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.provider import MarketDataProvider, TimeSpan

__all__ = ["BackfillReport", "run_backfill", "backfill"]


@dataclass(frozen=True)
class BackfillReport:
    planned: int
    fetched: int
    stored: int
    coverage: tuple[CoverageSpan, ...]


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def _read_spans(
    coverage: object | None,
    spans: Sequence[CoverageSpan],
    *,
    conn: object,
    instrument_id: str,
    timeframe: Timeframe,
) -> list[CoverageSpan]:
    if coverage is not None and hasattr(coverage, "list_spans"):
        result = await _maybe_await(
            coverage.list_spans(conn, instrument_id, timeframe)  # type: ignore[attr-defined]
        )
        return list(result)
    return list(spans)


async def _read_candles(
    store: CandleStore,
    fallback: Sequence[CandleRecord],
    *,
    conn: object,
    key: SeriesKey,
    start: datetime,
    end: datetime,
) -> list[CandleRecord]:
    if hasattr(store, "query"):
        result = await store.query(conn, key, start, end, None)
        return list(result)
    return list(fallback)


async def _write_candles(
    store: CandleStore,
    *,
    conn: object,
    batch_id: UUID,
    candles: list[CandleRecord],
) -> int:
    if hasattr(store, "upsert_batch"):
        return await store.upsert_batch(conn, batch_id, candles)
    return await store.write_batch(conn, batch_id, candles)  # type: ignore[attr-defined]


async def _record_coverage(
    coverage: object | None,
    span: CoverageSpan,
    *,
    conn: object,
    all_spans: list[CoverageSpan],
) -> None:
    merged = merge_spans([*all_spans, span])
    all_spans[:] = merged
    if coverage is None:
        return
    if hasattr(coverage, "replace_spans"):
        await _maybe_await(coverage.replace_spans(conn, merged))  # type: ignore[attr-defined]
    elif hasattr(coverage, "record_span"):
        await _maybe_await(coverage.record_span(span))  # type: ignore[attr-defined]
    elif hasattr(coverage, "upsert_span"):
        # The existing DC-8 repository exposes this method.  It is used only
        # for a newly declared interval; callers that need replacement of an
        # overlapping declaration should expose replace_spans.
        await _maybe_await(coverage.upsert_span(conn, span))  # type: ignore[attr-defined]


async def run_backfill(
    *,
    listing: VenueListing,
    key: SeriesKey,
    timeframe: Timeframe,
    calendar: VenueCalendar,
    range_start: datetime,
    range_end: datetime,
    provider: MarketDataProvider,
    store: CandleStore,
    conn: object = None,
    coverage: object | None = None,
    spans: Sequence[CoverageSpan] = (),
    candles: Sequence[CandleRecord] = (),
    asset_class: AssetClass = AssetClass.CRYPTO,
    quality_grade: QualityGrade = QualityGrade.RAW,
    batch_id_factory: Callable[[], UUID] = uuid4,
) -> BackfillReport:
    """Fetch each planned gap once, storing and declaring it before continuing.

    The provider is called once per gap and exceptions are intentionally allowed
    to propagate.  Consequently, a failure leaves earlier intervals committed
    by the injected store/registry and a later invocation plans only the
    remaining gaps.
    """
    coverage_spans = await _read_spans(
        coverage,
        spans,
        conn=conn,
        instrument_id=listing.instrument_id,
        timeframe=timeframe,
    )
    stored_candles = await _read_candles(
        store,
        candles,
        conn=conn,
        key=key,
        start=range_start,
        end=range_end,
    )
    gaps = plan_fetch(
        spans=coverage_spans,
        candles=stored_candles,
        tf=timeframe,
        calendar=calendar,
        range_start=range_start,
        range_end=range_end,
    )

    fetched = 0
    stored = 0
    for gap in gaps:
        columns = await provider.fetch_candles(
            listing,
            timeframe,
            TimeSpan(start=gap.start_at, end=gap.end_at),
        )
        fetched += 1
        if isinstance(columns, CandleColumns):
            fetched_candles = to_candle_records(columns, key)
        else:
            fetched_candles = list(columns)  # type: ignore[arg-type]
            fetched_candles = [c.model_copy(update={"key": key}) for c in fetched_candles]
        fetched_candles = [
            candle for candle in fetched_candles if gap.start_at <= candle.open_time < gap.end_at
        ]
        stored += await _write_candles(
            store,
            conn=conn,
            batch_id=batch_id_factory(),
            candles=fetched_candles,
        )
        stored_candles.extend(fetched_candles)

        template = next(
            (
                span
                for span in coverage_spans
                if span.instrument_id == listing.instrument_id
                and span.venue == listing.venue
                and span.timeframe == timeframe
            ),
            None,
        )
        coverage_span = CoverageSpan(
            instrument_id=listing.instrument_id,
            venue=listing.venue,
            asset_class=template.asset_class if template else asset_class,
            timeframe=timeframe,
            quality_grade=template.quality_grade if template else quality_grade,
            start_at=gap.start_at,
            end_at=gap.end_at,
        )
        await _record_coverage(
            coverage,
            coverage_span,
            conn=conn,
            all_spans=coverage_spans,
        )

    return BackfillReport(
        planned=len(gaps),
        fetched=fetched,
        stored=stored,
        coverage=tuple(coverage_spans),
    )


backfill = run_backfill
