"""DC-16 — application/backfill_job 단위 테스트(포트는 인메모리 가짜로 주입).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16.

DoD: 갭 계획→백필→커버리지 갱신 왕복이 실제로 갭을 없애는 것, 중단(provider
실패) 후 재실행이 이미 저장된 구간을 다시 채우지 않고 남은 갭만 처리하는
것("중단 후 재개"), 빈 provider 응답이 커버리지로 조용히 둔갑하지 않는 것
(§4.1), venue 축이 어긋난 요청이 fail-closed 거부되는 것을 검증한다.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import (
    VenueMismatchError,
    run_backfill_job,
)
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
)
from src.foundation.market_data.ports.coverage_repository import (
    CoverageSpan as StoredCoverageSpan,
)
from src.foundation.market_data.ports.provider import (
    ProviderCapabilities,
    TimeSpan,
)

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

    async def subscribe(self, listings: Sequence[VenueListing]) -> AsyncIterator[object]:
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
        conn=object(),  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        coverage_repo=coverage_repo,  # type: ignore[arg-type]
        listing=listing or _listing(),
        series_key=series_key or _series_key(),
        tf=Timeframe.H1,
        calendar=_calendar(),
        asset_class=AssetClass.CRYPTO,
        quality=CoverageQuality.PROVISIONAL,
        range_start=range_start,
        range_end=range_end,
    )


@pytest.mark.asyncio
async def test_backfill_fills_gap_then_second_run_finds_nothing_left() -> None:
    start, end = _dt(0), _dt(4)
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 1
    assert len(result.segments) == 1
    assert result.segments[0].stored == 4
    assert result.segments[0].span is not None
    assert coverage_repo.spans == [result.segments[0].span]
    assert len(result.merged_coverage) == 1
    assert result.merged_coverage[0].start_at == start
    assert result.merged_coverage[0].end_at == end

    # 재실행 — 이미 다 채워졌으니 더 이상 갭도, provider 호출도 없다(멱등).
    provider.calls.clear()
    second = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert second.gaps_planned == 0
    assert second.segments == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_resume_after_interruption_only_refetches_remaining_gap() -> None:
    """가운데 구간(01:00-02:00)은 이미 커버된 상태에서 시작 — 앞뒤 두 갭이
    생긴다. provider가 첫 갭에서 예외를 던지면, 그 갭 이전엔 아무것도
    저장되지 않지만(store 호출조차 없음) 재호출 시 여전히 두 갭이 남는다.
    이후 provider가 둘 다 답하면 재실행 한 번으로 남은 갭이 전부 채워진다
    (재개 = plan_fetch가 최신 DB 상태를 다시 읽는 것, 별도 상태 불필요)."""
    start, end = _dt(0), _dt(4)
    covered_start, covered_end = _dt(1), _dt(2)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    await coverage_repo.upsert_span(
        object(),
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=covered_start,
            end=covered_end,
        ),
    )
    await store.upsert_batch(
        object(),
        uuid4(),
        [
            CandleRecord(
                key=_series_key(),
                open_time=_dt(1),
                close_time=_dt(2),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        ],
    )

    failing_provider = _FakeProvider({})  # 등록된 답이 없으니 첫 호출에서 KeyError
    with pytest.raises(KeyError):
        await _run(failing_provider, store, coverage_repo, range_start=start, range_end=end)
    assert coverage_repo.spans == [
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=covered_start,
            end=covered_end,
        )
    ]  # 중단 지점 이전엔 아무 변화 없음

    working_provider = _FakeProvider(
        {
            (start, covered_start): _columns([0]),
            (covered_end, end): _columns([2, 3]),
        }
    )
    result = await _run(working_provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 2
    assert {seg.stored for seg in result.segments} == {1, 2}
    assert len(coverage_repo.spans) == 3  # 기존 1개 + 새로 채운 2개

    # 세 번째 실행 — 이제 전 구간이 커버돼 갭이 없다.
    final = await _run(working_provider, store, coverage_repo, range_start=start, range_end=end)
    assert final.gaps_planned == 0


@pytest.mark.asyncio
async def test_empty_provider_response_does_not_fabricate_coverage() -> None:
    """§4.1 조용한 0 채움 금지 — provider가 빈 응답을 주면 span을 만들지
    않고, 다음 실행에서도 같은 갭이 다시 잡힌다."""
    start, end = _dt(0), _dt(4)
    provider = _FakeProvider({(start, end): _columns([])})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 1
    assert result.segments[0].stored == 0
    assert result.segments[0].span is None
    assert coverage_repo.spans == []

    again = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert again.gaps_planned == 1  # 여전히 같은 갭 — 빈 응답이 커버로 둔갑하지 않았다


@pytest.mark.asyncio
async def test_venue_mismatch_between_listing_and_series_key_is_rejected() -> None:
    provider = _FakeProvider({})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    mismatched_key = SeriesKey(
        venue=Venue.KIS_KRX, instrument_id=_INSTRUMENT_ID, timeframe=Timeframe.H1
    )

    with pytest.raises(VenueMismatchError):
        await _run(
            provider,
            store,
            coverage_repo,
            range_start=_dt(0),
            range_end=_dt(4),
            series_key=mismatched_key,
        )
    assert provider.calls == []
    assert store.rows == {}
