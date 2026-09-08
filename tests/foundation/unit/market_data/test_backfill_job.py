"""DC-16 — `application/backfill_job.run_backfill` unit tests.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16 (preceded by DC-7 (task-1178), DC-11 (task-1187), DC-13 (task-1212),
all done).

All three ports (`MarketDataProvider`, `CandleStore`, `CoverageSpanStore`)
are injected as in-memory fakes — no real DB, no HTTP. DoD asserted here
(all falsifiable):
  (a) resume idempotency: replaying the same request against a coverage
      store that already reflects a prior successful run makes exactly 0
      provider fetch calls.
  (b) interrupt-then-resume: with 5 planned gaps, a provider exception on
      the 3rd fetch leaves the first 2 spans recorded and propagates the
      exception (not swallowed); resuming afterwards fetches exactly the
      remaining 3.
  (c) rejected input: `plan_fetch`'s `IndeterminateCoverageError` (e.g. a
      venue/calendar axis mismatch) propagates unchanged — it must not be
      turned into an empty, "successful" plan.
  (d) zero re-implementation: the module never computes gaps itself (no
      `timedelta` arithmetic in `backfill_job.py`), it only calls
      `plan_fetch`.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application import backfill_job
from src.foundation.market_data.application.backfill_job import (
    CoverageSpanStore,
    run_backfill,
)
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.coverage.gaps import IndeterminateCoverageError
from src.foundation.market_data.domain.timeframe import duration

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _calendar(venue: Venue) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _dt(hour: int) -> datetime:
    return datetime(2026, 9, 4, hour, 0, tzinfo=timezone.utc)


def _listing(venue: Venue = Venue.BITGET) -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=venue,
        venue_symbol="BTCUSDT",
        listed_at=_dt(0),
        delisted_at=None,
        is_primary=True,
    )


def _span(start_at: datetime, end_at: datetime, venue: Venue = Venue.BITGET) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=_ULID,
        venue=venue,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.RAW,
        start_at=start_at,
        end_at=end_at,
    )


class _FakeProvider:
    """Records call count; raises once at `raise_on_call` (1-indexed) if set."""

    def __init__(self, raise_on_call: int | None = None) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self._raise_on_call = raise_on_call

    def capabilities(self) -> object:  # pragma: no cover - unused by run_backfill
        raise NotImplementedError

    async def list_instruments(self, asset_class: object) -> object:  # pragma: no cover
        raise NotImplementedError

    async def fetch_candles(self, listing: object, tf: object, span: object) -> CandleColumns:
        self.calls.append((span.start, span.end))
        if self._raise_on_call is not None and len(self.calls) == self._raise_on_call:
            raise RuntimeError("provider unavailable")
        return CandleColumns(
            ts=[span.start],
            open=[Decimal("100")],
            high=[Decimal("110")],
            low=[Decimal("90")],
            close=[Decimal("105")],
            volume=[Decimal("10")],
            quote_volume=[None],
        )

    async def subscribe(self, listings: object) -> object:  # pragma: no cover - unused
        raise NotImplementedError


class _FakeCandleStore:
    def __init__(self) -> None:
        self.upsert_calls = 0

    async def upsert_batch(self, conn: object, batch_id: object, candles: object) -> int:
        self.upsert_calls += 1
        return len(candles)  # type: ignore[arg-type]

    async def quarantine(
        self, conn: object, batch_id: object, candles: object, issues: object
    ) -> None:
        raise NotImplementedError

    async def query(
        self, conn: object, key: object, start: object, end: object, as_of: object
    ) -> object:
        raise NotImplementedError

    async def last_open_time(self, conn: object, key: object) -> object:
        raise NotImplementedError

    async def read_candles_columnar(
        self, conn: object, key: object, start: object, end: object, as_of: object
    ) -> object:
        raise NotImplementedError


class _FakeCoverageStore(CoverageSpanStore):
    def __init__(self, spans: list[CoverageSpan]) -> None:
        self.spans = list(spans)
        self.recorded: list[CoverageSpan] = []

    async def list_spans(self) -> list[CoverageSpan]:
        return list(self.spans)

    async def record_span(self, span: CoverageSpan) -> None:
        self.spans.append(span)
        self.recorded.append(span)


def _known_candle(open_time: datetime) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.H1)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + duration(Timeframe.H1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


def _five_gap_setup() -> tuple[list[CoverageSpan], list[CandleRecord], VenueCalendar]:
    """Declares spans for hours 0,2,4,6,8 and leaves 1,3,5,7,9 undeclared ->
    5 disjoint NOT_COVERED gaps of exactly 1 hour each over [00:00, 10:00).
    `known_candles` supplies a stored candle for every hour in the range up
    front (spanning both covered and to-be-backfilled hours) so that once a
    gap's span gets declared (by this job, mid-test), it does not also
    surface as MISSING_CANDLES — this mirrors a caller that queried the
    candle store for the whole window before invoking `run_backfill`."""
    covered_hours = (0, 2, 4, 6, 8)
    spans = [_span(_dt(h), _dt(h + 1)) for h in covered_hours]
    candles = [_known_candle(_dt(h)) for h in range(10)]
    return spans, candles, _calendar(Venue.BITGET)


async def _run(
    *,
    spans_store: _FakeCoverageStore,
    provider: _FakeProvider,
    store: _FakeCandleStore,
    calendar: VenueCalendar,
    known_candles: list[CandleRecord] = (),  # type: ignore[assignment]
) -> list[CoverageSpan]:
    return await run_backfill(
        conn=object(),  # type: ignore[arg-type]
        listing=_listing(),
        series_key=SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.H1),
        tf=Timeframe.H1,
        asset_class=AssetClass.CRYPTO,
        quality_grade=QualityGrade.RAW,
        calendar=calendar,
        range_start=_dt(0),
        range_end=_dt(10),
        known_candles=known_candles,
        coverage=spans_store,
        provider=provider,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
    )


async def test_resume_is_idempotent_zero_fetch_calls_on_replay() -> None:
    spans, candles, calendar = _five_gap_setup()
    coverage = _FakeCoverageStore(spans)
    store = _FakeCandleStore()
    first_provider = _FakeProvider()

    await _run(
        spans_store=coverage,
        provider=first_provider,
        store=store,
        calendar=calendar,
        known_candles=candles,
    )
    assert len(first_provider.calls) == 5
    assert store.upsert_calls == 5

    second_provider = _FakeProvider()
    result = await _run(
        spans_store=coverage,
        provider=second_provider,
        store=store,
        calendar=calendar,
        known_candles=candles,
    )
    assert second_provider.calls == []
    assert result == [_span(_dt(0), _dt(10))]


async def test_interrupt_persists_partial_progress_and_propagates_exception() -> None:
    spans, candles, calendar = _five_gap_setup()
    coverage = _FakeCoverageStore(spans)
    store = _FakeCandleStore()
    provider = _FakeProvider(raise_on_call=3)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await _run(
            spans_store=coverage,
            provider=provider,
            store=store,
            calendar=calendar,
            known_candles=candles,
        )

    assert len(provider.calls) == 3
    assert store.upsert_calls == 2
    assert coverage.recorded == [_span(_dt(1), _dt(2)), _span(_dt(3), _dt(4))]

    resume_provider = _FakeProvider()
    result = await _run(
        spans_store=coverage,
        provider=resume_provider,
        store=store,
        calendar=calendar,
        known_candles=candles,
    )
    assert len(resume_provider.calls) == 3
    assert resume_provider.calls == [(_dt(5), _dt(6)), (_dt(7), _dt(8)), (_dt(9), _dt(10))]
    assert result == [_span(_dt(0), _dt(10))]


async def test_indeterminate_coverage_error_propagates_not_swallowed() -> None:
    """Span venue (KIS_KRX) mismatched with the calendar (BITGET) axis ->
    `plan_fetch` fail-closed rejects instead of returning an empty plan."""
    mismatched_span = _span(_dt(0), _dt(1), venue=Venue.KIS_KRX)
    coverage = _FakeCoverageStore([mismatched_span])
    store = _FakeCandleStore()
    provider = _FakeProvider()

    with pytest.raises(IndeterminateCoverageError):
        await _run(
            spans_store=coverage,
            provider=provider,
            store=store,
            calendar=_calendar(Venue.BITGET),
        )

    assert provider.calls == []
    assert store.upsert_calls == 0
    assert coverage.recorded == []


def test_no_reimplemented_gap_arithmetic_in_backfill_job() -> None:
    """(d) 재구현 0 — `plan_fetch` 호출 1건뿐이고 파일 내 `timedelta` 산술이
    없다(grep 단언)."""
    source = inspect.getsource(backfill_job)
    assert "timedelta" not in source
    assert source.count("plan_fetch(") == 1
