from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import run_backfill
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns

_INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_KEY_ID = UUID("00000000-0000-0000-0000-000000000001")
_START = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _calendar() -> VenueCalendar:
    session = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=session.tz, regular=session)


def _listing() -> VenueListing:
    return VenueListing(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_START,
        delisted_at=None,
        is_primary=True,
    )


def _key() -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=_KEY_ID, timeframe=Timeframe.H1)


def _candle(at: datetime) -> CandleRecord:
    return CandleRecord(
        key=_key(),
        open_time=at,
        close_time=at + timedelta(hours=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


def _columns(at: datetime) -> CandleColumns:
    return CandleColumns(
        ts=[at],
        open=[Decimal("100")],
        high=[Decimal("101")],
        low=[Decimal("99")],
        close=[Decimal("100")],
        volume=[Decimal("1")],
        quote_volume=[None],
    )


def _span() -> CoverageSpan:
    return CoverageSpan(
        instrument_id=_INSTRUMENT,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=Timeframe.H1,
        quality_grade=QualityGrade.RAW,
        start_at=_START,
        end_at=_START + timedelta(hours=10),
    )


class FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.fail_on_call: int | None = 3

    async def fetch_candles(self, listing, timeframe, span):
        self.calls.append((span.start, span.end))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("interrupted")
        return _columns(span.start)


class FakeStore:
    def __init__(self) -> None:
        self.candles: dict[datetime, CandleRecord] = {}

    async def query(self, conn, key, start, end, as_of):
        return [self.candles[t] for t in sorted(self.candles) if start <= t < end]

    async def upsert_batch(self, conn, batch_id, candles):
        new = [c for c in candles if c.open_time not in self.candles]
        self.candles.update({c.open_time: c for c in new})
        return len(new)


class FakeCoverage:
    def __init__(self) -> None:
        self.spans = [_span()]

    async def list_spans(self, conn, instrument_id, timeframe):
        return list(self.spans)

    async def record_span(self, span):
        self.spans = [span] if not self.spans else [
            CoverageSpan(
                instrument_id=self.spans[0].instrument_id,
                venue=self.spans[0].venue,
                asset_class=self.spans[0].asset_class,
                timeframe=self.spans[0].timeframe,
                quality_grade=self.spans[0].quality_grade,
                start_at=min(self.spans[0].start_at, span.start_at),
                end_at=max(self.spans[0].end_at, span.end_at),
            )
        ]


async def _run(provider, store, coverage):
    return await run_backfill(
        listing=_listing(),
        key=_key(),
        timeframe=Timeframe.H1,
        calendar=_calendar(),
        range_start=_START,
        range_end=_START + timedelta(hours=10),
        provider=provider,
        store=store,
        coverage=coverage,
    )


@pytest.mark.asyncio
async def test_interruption_resumes_from_the_first_unstored_gap() -> None:
    provider = FakeProvider()
    store = FakeStore()
    store.candles.update({_START + timedelta(hours=h): _candle(_START + timedelta(hours=h))
                          for h in (1, 3, 5, 7, 9)})
    coverage = FakeCoverage()

    with pytest.raises(RuntimeError, match="interrupted"):
        await _run(provider, store, coverage)
    assert len(provider.calls) == 3
    assert len(store.candles) == 7

    provider.fail_on_call = None
    report = await _run(provider, store, coverage)
    assert report.planned == 3
    assert len(provider.calls) == 6
    assert len(store.candles) == 10


@pytest.mark.asyncio
async def test_replaying_a_completed_request_does_not_fetch_again() -> None:
    provider = FakeProvider()
    provider.fail_on_call = None
    store = FakeStore()
    store.candles.update({_START + timedelta(hours=h): _candle(_START + timedelta(hours=h))
                          for h in (1, 3, 5, 7, 9)})
    coverage = FakeCoverage()

    await _run(provider, store, coverage)
    calls = len(provider.calls)
    report = await _run(provider, store, coverage)

    assert report.planned == 0
    assert len(provider.calls) == calls
