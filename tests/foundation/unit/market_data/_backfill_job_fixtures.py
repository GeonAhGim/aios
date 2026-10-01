"""DC-16 `application/backfill_job` 테스트 공유 fixture/인메모리 가짜.

RATCHET-split(task-10466): test_backfill_job.py가 500줄 임계값에 닿아, 핵심
갭-채움/멱등성 테스트와 재개·동시성·실패주입 테스트(test_backfill_job_resilience.py)로
나누면서 이 공유 모듈로 fixture/헬퍼를 뽑았다(tests/integration/services/
_exposure_snapshot_fixtures.py와 같은 선례 패턴).

# ratchet-allow: 인메모리 가짜 provider/store가 이 파일이 쓰지 않는 인터페이스
# 메서드에 대해 fail-closed 스텁으로 NotImplementedError를 낸다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import run_backfill_job
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.coverage_repository import CoverageQuality
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.market_data.ports.provider import (
    ProviderCandle,
    ProviderCapabilities,
    ProviderTick,
    TimeSpan,
)

__all__ = [
    "_ULID",
    "_INSTRUMENT_ID",
    "_dt",
    "_calendar",
    "_listing",
    "_series_key",
    "_columns",
    "_FakeProvider",
    "_FakeCandleStore",
    "_FakeCoverageRepository",
    "_run",
]

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _dt(hour: int, day: int = 4) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _listing() -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_dt(0, day=1),
        delisted_at=None,
        is_primary=True,
    )


def _series_key(tf: Timeframe = Timeframe.H1) -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=_INSTRUMENT_ID, timeframe=tf)


def _columns(hours: Sequence[int], day: int = 4) -> CandleColumns:
    ts = [_dt(h, day=day) for h in hours]
    n = len(ts)
    return CandleColumns(
        ts=ts,
        open=[Decimal("100")] * n,
        high=[Decimal("110")] * n,
        low=[Decimal("90")] * n,
        close=[Decimal("105")] * n,
        volume=[Decimal("10")] * n,
        quote_volume=[None] * n,
    )


class _FakeProvider:
    """`answers`에 등록된 `(start, end)` 구간만 응답한다 — 등록되지 않은
    구간을 요청하면 KeyError(진짜 provider가 예상 못 한 요청을 받으면
    터지는 것과 같은 실패 모드를 흉내)."""

    def __init__(self, answers: dict[tuple[datetime, datetime], CandleColumns]) -> None:
        self._answers = dict(answers)
        self.calls: list[TimeSpan] = []

    def capabilities(self) -> ProviderCapabilities:  # pragma: no cover - 미사용
        raise NotImplementedError

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        return []

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        self.calls.append(span)
        return self._answers[(span.start, span.end)]

    async def subscribe(
        self, listings: Sequence[VenueListing]
    ) -> AsyncIterator[ProviderTick | ProviderCandle]:
        raise NotImplementedError


class _FakeCandleStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[Venue, UUID, Timeframe], list[CandleRecord]] = {}

    async def upsert_batch(self, conn: object, batch_id: UUID, candles: list[CandleRecord]) -> int:
        key = (candles[0].key.venue, candles[0].key.instrument_id, candles[0].key.timeframe)
        existing = self.rows.setdefault(key, [])
        existing_times = {c.open_time for c in existing}
        added = 0
        for candle in candles:
            if candle.open_time not in existing_times:
                existing.append(candle)
                existing_times.add(candle.open_time)
                added += 1
        return added

    async def quarantine(
        self, conn: object, batch_id: UUID, candles: object, issues: object
    ) -> None:
        raise NotImplementedError

    async def query(
        self,
        conn: object,
        key: SeriesKey,
        start: datetime,
        end: datetime,
        as_of: datetime | None,
    ) -> list[CandleRecord]:
        rows = self.rows.get((key.venue, key.instrument_id, key.timeframe), [])
        return sorted((c for c in rows if start <= c.open_time < end), key=lambda c: c.open_time)

    async def last_open_time(self, conn: object, key: SeriesKey) -> datetime | None:
        rows = self.rows.get((key.venue, key.instrument_id, key.timeframe), [])
        return max((c.open_time for c in rows), default=None)

    async def read_candles_columnar(
        self, conn: object, key: SeriesKey, start: datetime, end: datetime, as_of: datetime | None
    ) -> CandleColumns:
        raise NotImplementedError


class _FakeCoverageRepository:
    def __init__(self) -> None:
        self.spans: list[StoredCoverageSpan] = []

    async def upsert_span(self, conn: object, span: StoredCoverageSpan) -> StoredCoverageSpan:
        for existing in self.spans:
            same_axis = (
                existing.instrument_id == span.instrument_id
                and existing.venue is span.venue
                and existing.timeframe is span.timeframe
                and existing.quality is span.quality
            )
            if same_axis and span.start < existing.end and existing.start < span.end:
                raise ValueError(f"겹치는 커버리지 구간: {span!r} vs {existing!r}")
        self.spans.append(span)
        return span

    async def list_spans(
        self, conn: object, instrument_id: str, timeframe: Timeframe
    ) -> list[StoredCoverageSpan]:
        matching = (
            s for s in self.spans if s.instrument_id == instrument_id and s.timeframe is timeframe
        )
        return sorted(matching, key=lambda s: s.start)


async def _run(
    provider: _FakeProvider,
    store: _FakeCandleStore,
    coverage_repo: _FakeCoverageRepository,
    *,
    range_start: datetime,
    range_end: datetime,
    listing: VenueListing | None = None,
    series_key: SeriesKey | None = None,
):
    return await run_backfill_job(
        conn=cast(asyncpg.Connection, object()),
        provider=provider,
        store=store,
        coverage_repo=coverage_repo,
        listing=listing or _listing(),
        series_key=series_key or _series_key(),
        tf=Timeframe.H1,
        calendar=_calendar(),
        asset_class=AssetClass.CRYPTO,
        quality=CoverageQuality.PROVISIONAL,
        range_start=range_start,
        range_end=range_end,
    )
