"""DC-16 백필 유스케이스 단위 테스트 — 실 DB 없이 포트 fake만 주입한다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import BackfillRequest, backfill
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns

_INSTRUMENT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_SERIES_ID = UUID("00000000-0000-0000-0000-000000000001")
_START = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)


def _request() -> BackfillRequest:
    listing = VenueListing(
        instrument_id=_INSTRUMENT_ID,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_START,
        delisted_at=None,
        is_primary=True,
    )
    return BackfillRequest(
        listing=listing,
        key=SeriesKey(venue=Venue.BITGET, instrument_id=_SERIES_ID, timeframe=Timeframe.H1),
        asset_class=AssetClass.CRYPTO,
        quality_grade=QualityGrade.RAW,
        calendar=VenueCalendar(
            venue=Venue.BITGET,
            tz=KNOWN_SESSIONS[Venue.BITGET.value].tz,
            regular=KNOWN_SESSIONS[Venue.BITGET.value],
        ),
        range_start=_START,
        range_end=_START + timedelta(hours=5),
    )


def _columns(start: datetime) -> CandleColumns:
    return CandleColumns(
        ts=[start],
        open=[Decimal("100")],
        high=[Decimal("101")],
        low=[Decimal("99")],
        close=[Decimal("100")],
        volume=[Decimal("1")],
        quote_volume=[None],
    )


class _Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.fail_on_call: int | None = None

    def capabilities(self): ...

    async def list_instruments(self, asset_class): ...

    async def fetch_candles(self, listing, tf, span):
        self.calls.append((span.start, span.end))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("provider interrupted")
        return _columns(span.start)

    async def subscribe(self, listings): ...


class _Store:
    def __init__(self) -> None:
        self.candles = []
        self.calls = 0

    async def upsert_batch(self, conn, batch_id, candles) -> int:
        self.calls += 1
        self.candles.extend(candles)
        return len(candles)

    async def quarantine(self, conn, batch_id, candles, issues) -> None: ...

    async def query(self, conn, key, start, end, as_of): ...

    async def last_open_time(self, conn, key): ...

    async def read_candles_columnar(self, conn, key, start, end, as_of): ...


@pytest.mark.asyncio
async def test_resume_is_idempotent_and_does_not_fetch_covered_gaps() -> None:
    request = _request()
    provider = _Provider()
    store = _Store()
    spans = []
    candles = []

    await backfill(
        request,
        provider=provider,
        store=store,
        coverage_spans=spans,
        candles=candles,
    )
    calls_after_first_run = len(provider.calls)

    result = await backfill(
        request,
        provider=provider,
        store=store,
        coverage_spans=spans,
        candles=candles,
    )

    assert calls_after_first_run == 5
    assert len(provider.calls) == calls_after_first_run
    assert result.fetched_gaps == ()


@pytest.mark.asyncio
async def test_interruption_keeps_completed_segments_for_resume() -> None:
    request = _request()
    provider = _Provider()
    provider.fail_on_call = 3
    store = _Store()
    spans = []
    candles = []

    with pytest.raises(RuntimeError, match="interrupted"):
        await backfill(
            request,
            provider=provider,
            store=store,
            coverage_spans=spans,
            candles=candles,
        )

    assert len(provider.calls) == 3
    assert len(spans) == 1
    assert (spans[0].start_at, spans[0].end_at) == (_START, _START + timedelta(hours=2))
    assert len(candles) == 2
    assert store.calls == 2

    provider.fail_on_call = None
    await backfill(
        request,
        provider=provider,
        store=store,
        coverage_spans=spans,
        candles=candles,
    )

    assert len(provider.calls) == 6
    assert len(candles) == 5
    assert len(spans) == 1
    assert (spans[0].start_at, spans[0].end_at) == (
        _START,
        _START + timedelta(hours=5),
    )
