"""DC-16 `application/backfill_job.py` 단위 테스트 — 실DB 없이 포트를
인메모리 가짜 구현으로 주입한다.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-16(선행 DC-7·DC-11·DC-13), §9.2 DC-16.

DoD (a) 재개 멱등: 이미 커버된 구간을 포함한 동일 요청을 두 번 실행하면
2회차의 provider fetch 호출 횟수가 정확히 0이다 —
`test_second_run_over_same_range_calls_provider_zero_times`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import (
    BackfillReport,
    BackfillRequest,
    run_backfill,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    SeriesKey,
    SessionWindow,
    Timeframe,
    Venue,
)
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.coverage.gaps import IndeterminateCoverageError
from src.foundation.market_data.domain.timeframe import expected_opens
from src.foundation.market_data.ports.provider import (
    ProviderCapabilities,
    TickOrCandle,
    TimeSpan,
)

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_SERIES_INSTRUMENT_ID = UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


def _calendar(venue: Venue = Venue.BITGET) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _dt(hour: int, day: int = 4) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _listing(venue: Venue = Venue.BITGET) -> VenueListing:
    return VenueListing(
        instrument_id=_ULID,
        venue=venue,
        venue_symbol="BTCUSDT",
        listed_at=_dt(0, day=1),
        delisted_at=None,
        is_primary=True,
    )


class FakeProvider:
    """`MarketDataProvider` SPI 위장 — `fetch_candles`만 실제로 쓰고, 매
    호출을 기록한다(테스트가 호출 횟수를 단언할 수 있게). 나머지 Protocol
    메서드는 이 테스트가 호출하면 안 되므로 즉시 실패한다."""

    def __init__(self) -> None:
        self.fetch_calls: list[TimeSpan] = []

    def capabilities(self) -> ProviderCapabilities:
        raise AssertionError("capabilities는 이 테스트에서 호출되면 안 된다")

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        raise AssertionError("list_instruments는 이 테스트에서 호출되면 안 된다")

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        self.fetch_calls.append(span)
        session = SessionWindow(open_at=span.start, close_at=span.end, kind="REGULAR")
        opens = expected_opens(span.start, span.end, tf, [session])
        return CandleColumns(
            ts=opens,
            open=[Decimal("100")] * len(opens),
            high=[Decimal("110")] * len(opens),
            low=[Decimal("90")] * len(opens),
            close=[Decimal("105")] * len(opens),
            volume=[Decimal("10")] * len(opens),
            quote_volume=[None] * len(opens),
        )

    async def subscribe(self, listings: Sequence[VenueListing]) -> AsyncIterator[TickOrCandle]:
        raise AssertionError("subscribe는 이 테스트에서 호출되면 안 된다")


class FakeCandleStore:
    """`CandleStore` 포트 위장 — 딕셔너리에 저장, 재실행 시 `ON CONFLICT DO
    NOTHING`과 같은 멱등 의미론(같은 open_time은 다시 세지 않는다)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[Venue, UUID, Timeframe, datetime], CandleRecord] = {}
        self.upsert_calls = 0

    async def upsert_batch(
        self, conn: object, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        self.upsert_calls += 1
        stored = 0
        for candle in candles:
            row_key = (
                candle.key.venue,
                candle.key.instrument_id,
                candle.key.timeframe,
                candle.open_time,
            )
            if row_key not in self._rows:
                self._rows[row_key] = candle
                stored += 1
        return stored

    async def quarantine(
        self,
        conn: object,
        batch_id: object,
        candles: list[CandleRecord],
        issues: list[QualityIssue],
    ) -> None:
        raise AssertionError("quarantine은 이 테스트에서 호출되면 안 된다")

    async def query(
        self, conn: object, key: SeriesKey, start: datetime, end: datetime, as_of: object
    ) -> list[CandleRecord]:
        return sorted(
            (
                c
                for (venue, iid, tf, _open_time), c in self._rows.items()
                if venue == key.venue
                and iid == key.instrument_id
                and tf == key.timeframe
                and start <= c.open_time < end
            ),
            key=lambda c: c.open_time,
        )

    async def last_open_time(self, conn: object, key: SeriesKey) -> datetime | None:
        raise AssertionError("last_open_time은 이 테스트에서 호출되면 안 된다")

    async def read_candles_columnar(
        self, conn: object, key: SeriesKey, start: datetime, end: datetime, as_of: object
    ) -> CandleColumns:
        raise AssertionError("read_candles_columnar은 이 테스트에서 호출되면 안 된다")


class FakeCoverageSource:
    """`CoverageSource` 포트 위장 — `(instrument_id, tf)` 축별로 스팬 목록을
    그대로 들고 있는다(호출자가 이미 병합한 목록을 그대로 대체 기록)."""

    def __init__(self) -> None:
        self._by_axis: dict[tuple[str, Timeframe], list[CoverageSpan]] = {}
        self.record_calls = 0

    async def spans_for(self, instrument_id: str, tf: Timeframe) -> list[CoverageSpan]:
        return list(self._by_axis.get((instrument_id, tf), []))

    async def record_spans(self, spans: Sequence[CoverageSpan]) -> None:
        self.record_calls += 1
        for span in spans:
            self._by_axis[(span.instrument_id, span.timeframe)] = list(spans)


def _request(*, range_start: datetime, range_end: datetime) -> BackfillRequest:
    return BackfillRequest(
        coverage_instrument_id=_ULID,
        series_instrument_id=_SERIES_INSTRUMENT_ID,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        quality_grade=QualityGrade.RAW,
        tf=Timeframe.H1,
        listing=_listing(),
        calendar=_calendar(),
        range_start=range_start,
        range_end=range_end,
    )


async def _run(
    request: BackfillRequest,
    provider: FakeProvider,
    store: FakeCandleStore,
    coverage: FakeCoverageSource,
) -> BackfillReport:
    return await run_backfill(request, provider=provider, store=store, coverage=coverage, conn=None)


async def test_first_run_fetches_and_stores_full_gap_and_records_coverage() -> None:
    provider = FakeProvider()
    store = FakeCandleStore()
    coverage = FakeCoverageSource()
    request = _request(range_start=_dt(0), range_end=_dt(4))

    report = await _run(request, provider, store, coverage)

    assert len(provider.fetch_calls) == 1
    assert report.fetched_segments == 1
    assert report.segments[0].stored_count == 4
    spans = await coverage.spans_for(_ULID, Timeframe.H1)
    assert len(spans) == 1
    assert spans[0].start_at == _dt(0)
    assert spans[0].end_at == _dt(4)


async def test_second_run_over_same_range_calls_provider_zero_times() -> None:
    """DoD (a): 재개 멱등 — 이미 커버된 구간을 포함한 동일 요청을 두 번
    실행하면 2회차의 provider fetch 호출 횟수가 정확히 0."""
    provider = FakeProvider()
    store = FakeCandleStore()
    coverage = FakeCoverageSource()
    request = _request(range_start=_dt(0), range_end=_dt(4))

    first = await _run(request, provider, store, coverage)
    assert len(provider.fetch_calls) == 1
    assert first.fetched_segments == 1

    second = await _run(request, provider, store, coverage)

    assert len(provider.fetch_calls) == 1  # 누적 그대로 — 2회차에 0회 추가 호출
    assert second.gaps_planned == []
    assert second.fetched_segments == 0


async def test_resume_after_partial_interruption_only_fetches_remaining_gap() -> None:
    """중단 후 재개: 1회차 실행에서 이미 [00:00,02:00)이 저장·기록된 상태를
    흉내내고, 2회차는 나머지 [02:00,04:00)만 계획·백필해야 한다."""
    provider = FakeProvider()
    store = FakeCandleStore()
    coverage = FakeCoverageSource()

    partial_request = _request(range_start=_dt(0), range_end=_dt(2))
    await _run(partial_request, provider, store, coverage)
    assert len(provider.fetch_calls) == 1

    full_request = _request(range_start=_dt(0), range_end=_dt(4))
    report = await _run(full_request, provider, store, coverage)

    assert len(provider.fetch_calls) == 2
    assert report.fetched_segments == 1
    only_gap = report.gaps_planned[0]
    assert only_gap.start_at == _dt(2)
    assert only_gap.end_at == _dt(4)
    spans = await coverage.spans_for(_ULID, Timeframe.H1)
    assert len(spans) == 1
    assert spans[0].start_at == _dt(0)
    assert spans[0].end_at == _dt(4)


async def test_reversed_range_fails_closed_without_calling_provider() -> None:
    """negative: `plan_fetch`가 fail-closed 판정 불가로 예외를 던지면 이
    잡은 그 예외를 삼키지 않고 그대로 전파해야 한다 — provider도 이미 잘못된
    계획대로 호출되면 안 되므로 fetch 호출 횟수는 0이어야 한다."""
    provider = FakeProvider()
    store = FakeCandleStore()
    coverage = FakeCoverageSource()
    reversed_request = _request(range_start=_dt(4), range_end=_dt(0))

    with pytest.raises(IndeterminateCoverageError):
        await _run(reversed_request, provider, store, coverage)

    assert provider.fetch_calls == []
    assert store.upsert_calls == 0
    assert coverage.record_calls == 0

