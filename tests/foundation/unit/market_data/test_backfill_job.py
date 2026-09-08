from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.application.backfill_job import BackfillJob
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.provider import TimeSpan


_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_UUID = UUID("11111111-1111-1111-1111-111111111111")


def _dt(hour: int) -> datetime:
    return datetime(2026, 9, 4, hour, tzinfo=timezone.utc)


class _Provider:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[TimeSpan] = []
        self.fail_at = fail_at

    async def fetch_candles(self, listing, tf, span):
        self.calls.append(span)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError("interrupted")
        return CandleColumns(
            ts=[span.start],
            open=[Decimal("100")],
            high=[Decimal("101")],
            low=[Decimal("99")],
            close=[Decimal("100")],
            volume=[Decimal("1")],
            quote_volume=[None],
        )


class _Store:
    def __init__(self) -> None:
        self.candles: list[CandleRecord] = []

    async def query(self, conn, key, start, end, as_of):
        return [
            candle
            for candle in self.candles
            if candle.key == key and start <= candle.open_time < end
        ]

    async def upsert_batch(self, conn, batch_id, candles):
        existing = {(c.key, c.open_time) for c in self.candles}
        fresh = [c for c in candles if (c.key, c.open_time) not in existing]
        self.candles.extend(fresh)
        return len(fresh)


def _job(provider: _Provider, store: _Store) -> BackfillJob:
    session = KNOWN_SESSIONS[Venue.BITGET.value]
    job = BackfillJob(
        provider=provider,
        store=store,
        listing=VenueListing(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            venue_symbol="BTCUSDT",
            listed_at=_dt(0),
            delisted_at=None,
            is_primary=True,
        ),
        series_key=SeriesKey(
            venue=Venue.BITGET, instrument_id=_UUID, timeframe=Timeframe.H1
        ),
        asset_class=AssetClass.CRYPTO,
        calendar=VenueCalendar(venue=Venue.BITGET.value, tz=session.tz, regular=session),
        coverage_spans=[
            CoverageSpan(
                instrument_id=_ULID,
                venue=Venue.BITGET,
                asset_class=AssetClass.CRYPTO,
                timeframe=Timeframe.H1,
                quality_grade=QualityGrade.VALIDATED,
                start_at=_dt(0),
                end_at=_dt(10),
            )
        ],
    )
    store.candles = [
        CandleRecord(
            key=job._series_key,
            open_time=_dt(hour),
            close_time=_dt(hour + 1),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
        for hour in range(0, 10, 2)
    ]
    return job


@pytest.mark.asyncio
async def test_resume_is_idempotent_and_second_run_does_not_fetch() -> None:
    provider = _Provider()
    store = _Store()
    job = _job(provider, store)

    first = await job.run(_dt(0), _dt(10))
    calls_after_first = len(provider.calls)
    second = await job.run(_dt(0), _dt(10))

    assert first.gaps_planned == 5
    assert first.gaps_filled == 5
    assert len(job.coverage_spans) == 1
    assert second.gaps_planned == 0
    assert len(provider.calls) == calls_after_first


@pytest.mark.asyncio
async def test_interrupted_run_resumes_completed_gaps_only() -> None:
    provider = _Provider(fail_at=3)
    store = _Store()
    job = _job(provider, store)

    with pytest.raises(RuntimeError, match="interrupted"):
        await job.run(_dt(0), _dt(10))

    provider.fail_at = None
    calls_before_resume = len(provider.calls)
    result = await job.run(_dt(0), _dt(10))

    assert result.gaps_planned == 3
    assert len(provider.calls) == calls_before_resume + 3
    assert len(store.candles) == 10
