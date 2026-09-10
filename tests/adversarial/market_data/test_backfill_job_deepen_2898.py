"""DC-16 `application/backfill_job.py` -- DEEPEN(task-2898,
DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙.

기존 `tests/foundation/unit/market_data/test_backfill_job.py`는 negative
3건(§4.1 빈 응답, venue 축 불일치, provider가 미등록 구간을 요청받으면
KeyError로 실패)까지만 갖춰 D1이었다 -- 감사에서 "성능단언 없음, 게이트적색
재현 없음, D3 적대적/리플레이/다중 워커 증명 없음"으로 지적됐다(DC-16은
DC 축이라 D3 하한). 이 파일이 그 부족분을 채운다:

1. 실패주입 -- 기존 테스트가 안 건드린 두 지점. (a) provider가 §3.1 타입드
   에러(`DataProviderError`)로 실패할 때 KeyError처럼 삼켜지지 않고 그대로
   전파되는지. (b) `store.upsert_batch`가 성공한 *뒤* `coverage_repo.
   upsert_span`이 실패하면(기존 테스트는 항상 첫 갭에서 provider가 실패해
   store 호출 자체가 없었다) 캔들은 남고 커버리지 선언만 없는, 더 늦은
   단계의 부분 상태가 삼켜지지 않는지.
2. 성능단언 -- 분리된 갭 다수(250개)를 절대시간 예산 안에 처리하는지와
   provider 호출 수가 정확히 갭 수와 같은지(재조회 없음), 갭 수를 4배로
   늘려도 처리시간이 이차로 퇴화하지 않는지(`merged` 재병합이 갭마다 전체
   목록을 다시 스캔하므로 이론상 O(n^2)로 샐 수 있는 지점).
3. 게이트 적색 재현 -- (a) 첫 갭은 성공하고 두 번째 갭에서 실패하는 다중
   갭 시나리오에서 첫 갭의 저장·커버리지가 남아 있는지(기존 테스트는
   "실패 이전엔 항상 아무것도 없다"만 증명했다). (b) provider가 요청받은
   갭 전체가 아니라 앞부분만 응답해도 새 커버리지 span이 딱 실제 응답
   범위만 선언하는지(요청 범위를 그대로 써버리면 나머지가 거짓으로
   커버된 것으로 새어나간다 -- 이 테스트가 그 회귀를 잡는다).
4. D3 -- 서로 다른 instrument_id 3개를 같은 fake store/coverage_repo/
   provider에 `asyncio.gather`로 동시에 백필해도 서로 오염되지 않는지,
   고정시드 5개로 무작위 기존 커버리지 배치 위에 백필해도
   `_FakeCoverageRepository`가 흉내 내는 EXCLUDE류 겹침 거부에 한 번도
   걸리지 않고 완전히 수렴하는지(갭 계산이 조금이라도 어긋나면 즉시
   적색).

`backfill_job.py`는 한 줄도 고치지 않는다 -- 새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.application.backfill_job import (
    BackfillJobResult,
    run_backfill_job,
)
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
)
from src.foundation.market_data.ports.coverage_repository import (
    CoverageSpan as StoredCoverageSpan,
)
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    MarketDataProvider,
    ProviderCandle,
    ProviderCapabilities,
    ProviderTick,
    TimeSpan,
)

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_INSTRUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _dt(hour: int, day: int = 4) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


def _calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _listing(instrument_id: str = _ULID) -> VenueListing:
    return VenueListing(
        instrument_id=instrument_id,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        listed_at=_dt(0, day=1),
        delisted_at=None,
        is_primary=True,
    )


def _series_key(tf: Timeframe = Timeframe.H1, instrument_id: UUID = _INSTRUMENT_ID) -> SeriesKey:
    return SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=tf)


def _seed_covered_candles(
    store: _FakeCandleStore,
    spans: Sequence[StoredCoverageSpan],
    *,
    instrument_id: UUID = _INSTRUMENT_ID,
) -> None:
    """`plan_fetch`는 커버리지 선언뿐 아니라 실제 캔들 유무도 대조한다
    (`MISSING_CANDLES`) -- 선언(span)만 넣고 캔들을 안 채우면 그 구간이
    또 다른 갭으로 오판된다. 이 헬퍼가 span 범위 전체에 걸쳐 정확히
    선언과 일치하는 캔들을 채워, "이미 커버된 구간"을 진짜로 커버된
    상태로 만든다."""
    key = (Venue.BITGET, instrument_id, Timeframe.H1)
    rows = store.rows.setdefault(key, [])
    step = duration(Timeframe.H1)
    for span in spans:
        t = span.start
        while t < span.end:
            rows.append(
                CandleRecord(
                    key=_series_key(instrument_id=instrument_id),
                    open_time=t,
                    close_time=t + step,
                    open=Decimal("100"),
                    high=Decimal("110"),
                    low=Decimal("90"),
                    close=Decimal("105"),
                    volume=Decimal("10"),
                )
            )
            t += step


def _columns_from_times(ts: Sequence[datetime]) -> CandleColumns:
    n = len(ts)
    return CandleColumns(
        ts=list(ts),
        open=[Decimal("100")] * n,
        high=[Decimal("110")] * n,
        low=[Decimal("90")] * n,
        close=[Decimal("105")] * n,
        volume=[Decimal("10")] * n,
        quote_volume=[None] * n,
    )


def _columns(hours: Sequence[int], day: int = 4) -> CandleColumns:
    return _columns_from_times([_dt(h, day=day) for h in hours])


class _FakeProvider:
    """`answers`에 등록된 `(start, end)` 구간만 응답한다."""

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


class _FailingProvider(_FakeProvider):
    """등록된 응답이 없는 구간이 `fail_ranges`에 있으면 §3.1 타입드
    `DataProviderError`로 실패한다(KeyError가 아니라 진짜 벤더 어댑터가
    낼 법한 에러 taxonomy로 실패를 흉내)."""

    def __init__(
        self,
        answers: dict[tuple[datetime, datetime], CandleColumns],
        *,
        fail_ranges: frozenset[tuple[datetime, datetime]] = frozenset(),
    ) -> None:
        super().__init__(answers)
        self._fail_ranges = set(fail_ranges)

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        self.calls.append(span)
        key = (span.start, span.end)
        if key in self._fail_ranges:
            raise DataProviderError(
                DataProviderErrorCode.DATA_COVERAGE_MISSING,
                provider_id="deepen-2898-fake",
            )
        return self._answers[key]


class _EchoProvider:
    """요청받은 span을 그대로 타임프레임 간격으로 채워 항상 성공 응답한다
    -- 무작위 갭 경계를 사전에 알 필요 없는 fuzz/성능 테스트 전용."""

    def __init__(self) -> None:
        self.calls: list[TimeSpan] = []

    def capabilities(self) -> ProviderCapabilities:  # pragma: no cover - 미사용
        raise NotImplementedError

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        return []

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        self.calls.append(span)
        step = duration(tf)
        ts: list[datetime] = []
        t = span.start
        while t < span.end:
            ts.append(t)
            t += step
        return _columns_from_times(ts)

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
    """`upsert_span`이 같은 축 안에서 겹치는 구간을 `ValueError`로 거부한다
    -- 실 Postgres `coverage_spans` EXCLUDE 제약(§4.1)과 같은 의미론이라,
    `plan_fetch`가 계산한 갭이 조금이라도 기존 커버리지와 겹치면 아래
    게이트 적색/D3 테스트가 그 자리에서 적색이 된다."""

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


class _FlakyCoverageRepository(_FakeCoverageRepository):
    """`fail_after`번째 이후의 `upsert_span` 호출은 (DB 커넥션 유실 등을
    흉내 내어) `RuntimeError`로 실패한다. 실패는 `self.spans`에 반영되기
    *전에* 일어난다."""

    def __init__(self, *, fail_after: int) -> None:
        super().__init__()
        self.fail_after = fail_after
        self._calls = 0

    async def upsert_span(self, conn: object, span: StoredCoverageSpan) -> StoredCoverageSpan:
        self._calls += 1
        if self._calls > self.fail_after:
            raise RuntimeError("coverage repo unavailable (DB connection lost)")
        return await super().upsert_span(conn, span)


async def _run(
    provider: MarketDataProvider,
    store: _FakeCandleStore,
    coverage_repo: _FakeCoverageRepository,
    *,
    range_start: datetime,
    range_end: datetime,
    listing: VenueListing | None = None,
    series_key: SeriesKey | None = None,
) -> BackfillJobResult:
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


# ---- 실패 주입 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_provider_error_propagates_without_being_swallowed() -> None:
    """provider가 §3.1 타입드 `DataProviderError`(예: 커버리지 밖 구간)로
    실패하면, KeyError를 던지는 기존 테스트와 달리 이 예외의 `code`까지
    그대로 살아서 전파돼야 한다 -- 어딘가에서 예외를 뭉뚱그려 삼키면
    (예: `except Exception: pass`) 이 테스트가 적색이 된다."""
    start, end = _dt(0), _dt(4)
    provider = _FailingProvider({}, fail_ranges=frozenset({(start, end)}))
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    with pytest.raises(DataProviderError) as exc_info:
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert exc_info.value.code is DataProviderErrorCode.DATA_COVERAGE_MISSING
    assert store.rows == {}
    assert coverage_repo.spans == []


@pytest.mark.asyncio
async def test_coverage_upsert_failure_after_store_succeeds_leaves_candles_persisted() -> None:
    """기존 `test_resume_after_interruption_only_refetches_remaining_gap`는
    provider가 첫 갭에서 실패해 `store` 호출 자체가 없는 경우만 다룬다.
    여기서는 provider가 정상 응답하고 `store.upsert_batch`까지 성공한
    *뒤* `coverage_repo.upsert_span`이 실패하는, 더 늦은 단계의 부분
    상태를 검증한다 -- 캔들은 남아 있는데 커버리지 선언은 없는 상태가
    삼켜지지 않아야 다음 실행의 `plan_fetch`가 이 구간을 여전히 갭으로
    다시 잡을 수 있다."""
    start, end = _dt(0), _dt(4)
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})
    store = _FakeCandleStore()
    coverage_repo = _FlakyCoverageRepository(fail_after=0)

    with pytest.raises(RuntimeError, match="coverage repo unavailable"):
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    stored = store.rows[(Venue.BITGET, _INSTRUMENT_ID, Timeframe.H1)]
    assert {c.open_time for c in stored} == {_dt(0), _dt(1), _dt(2), _dt(3)}  # 캔들은 저장됐다
    assert coverage_repo.spans == []  # 그러나 커버리지 선언은 없다 -- 삼켜지지 않은 실패

    # 커버리지 저장소가 복구된 뒤 재실행 -- 캔들은 이미 있지만 커버리지
    # 선언이 없어 plan_fetch는 여전히 이 구간을 NOT_COVERED로 다시 잡는다.
    coverage_repo.fail_after = 999
    again = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert again.gaps_planned == 1
    assert again.segments[0].stored == 0  # 캔들은 이미 있어 dedup으로 신규 저장 0건
    assert again.segments[0].span is not None  # 그래도 커버리지는 이번엔 등록된다


# ---- 게이트 적색 재현 --------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_red_first_of_two_gaps_persists_before_second_gap_failure() -> None:
    """이미 커버된 중간 구간 때문에 갭이 두 개로 쪼개진 상태에서, 첫 갭은
    provider가 정상 응답하고 두 번째 갭에서 실패한다. 기존 실패주입
    테스트는 실패가 항상 첫 갭에서 일어나 "이전엔 아무것도 저장되지
    않는다"만 증명했다 -- 이 테스트는 실패 *이전* 갭의 저장·커버리지
    효과가 남아 있는지, 그리고 재실행이 이미 채운 첫 갭을 다시 요청하지
    않는지를 검증한다(순서가 뒤바뀌거나 이전 결과가 롤백되면 적색)."""
    start, end = _dt(0), _dt(6)
    covered_start, covered_end = _dt(2), _dt(3)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    covered_span = StoredCoverageSpan(
        instrument_id=_ULID,
        venue=Venue.BITGET,
        timeframe=Timeframe.H1,
        quality=CoverageQuality.PROVISIONAL,
        start=covered_start,
        end=covered_end,
    )
    await coverage_repo.upsert_span(object(), covered_span)
    _seed_covered_candles(store, [covered_span])  # 선언뿐 아니라 실제 캔들도 채운다
    provider = _FailingProvider(
        {(start, covered_start): _columns([0, 1])},
        fail_ranges=frozenset({(covered_end, end)}),
    )

    with pytest.raises(DataProviderError):
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    stored = store.rows[(Venue.BITGET, _INSTRUMENT_ID, Timeframe.H1)]
    assert {c.open_time for c in stored} == {_dt(0), _dt(1), _dt(2)}  # 첫 갭 + 기존 커버 구간
    assert len(coverage_repo.spans) == 2  # 기존 1개 + 새로 채운 첫 갭 1개
    assert provider.calls == [
        TimeSpan(start=start, end=covered_start),
        TimeSpan(start=covered_end, end=end),
    ]

    resumed_provider = _FakeProvider({(covered_end, end): _columns([4, 5])})
    result = await _run(resumed_provider, store, coverage_repo, range_start=start, range_end=end)
    assert result.gaps_planned == 1  # 첫 갭은 이미 채워졌으니 두 번째만 남는다
    assert resumed_provider.calls == [TimeSpan(start=covered_end, end=end)]


@pytest.mark.asyncio
async def test_gate_red_partial_provider_response_only_covers_returned_candles() -> None:
    """provider가 요청받은 갭 [0,10) 전체가 아니라 앞부분 [0,5)만 응답해도
    (공급자 보유 이력이 요청 구간보다 짧을 때 실제로 벌어지는 상황) 새
    커버리지 span은 딱 돌아온 캔들 범위만 선언해야 한다. 만약 구현이
    요청한 gap.end_at을 그대로 span.end로 써버리면(응답 범위를 요청
    범위로 착각) [5,10)이 근거 없이 커버된 것으로 거짓 선언되고, 다음
    재생에서 그 구간이 다시는 갭으로 잡히지 않아 이 테스트가 적색이
    된다."""
    start, end = _dt(0), _dt(10)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3, 4])})  # 앞 5시간만

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert result.gaps_planned == 1
    assert result.segments[0].span is not None
    assert result.segments[0].span.end == _dt(5)  # 요청 end(_dt(10))가 아니라 실응답 끝

    # 재생 -- 나머지 [5,10)이 여전히 갭으로 잡혀야 한다(과다 선언 없음).
    second_provider = _FakeProvider({(_dt(5), end): _columns([5, 6, 7, 8, 9])})
    second = await _run(second_provider, store, coverage_repo, range_start=start, range_end=end)
    assert second.gaps_planned == 1
    assert second.segments[0].span is not None
    assert second.segments[0].span.start == _dt(5)
    assert second.segments[0].span.end == end

    final = await _run(second_provider, store, coverage_repo, range_start=start, range_end=end)
    assert final.gaps_planned == 0


# ---- 성능 단언 ---------------------------------------------------------------


def _checkerboard(
    n_gaps: int,
) -> tuple[
    datetime, datetime, list[StoredCoverageSpan], dict[tuple[datetime, datetime], CandleColumns]
]:
    """짝수 시간대는 이미 커버, 홀수 시간대는 미커버로 둬 `plan_fetch`가
    매 홀수 시간마다 별도 `CoverageGap`을 내도록 한다(연속 구간이면
    `_coalesce`가 하나로 합쳐 갭 수를 인위적으로 부풀릴 수 없다 -- 이렇게
    하면 처리 비용이 실제 gap 개수에 비례하도록 강제된다)."""
    base = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)
    start = base
    end = base + timedelta(hours=2 * n_gaps)
    covered_spans = [
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=base + timedelta(hours=2 * i),
            end=base + timedelta(hours=2 * i + 1),
        )
        for i in range(n_gaps)
    ]
    answers = {
        (
            base + timedelta(hours=2 * i + 1),
            base + timedelta(hours=2 * i + 2),
        ): _columns_from_times([base + timedelta(hours=2 * i + 1)])
        for i in range(n_gaps)
    }
    return start, end, covered_spans, answers


@pytest.mark.perf
@pytest.mark.asyncio
async def test_backfill_job_meets_latency_budget_with_many_disjoint_gaps() -> None:
    n_gaps = 250
    budget_sec = 5.0  # 실측 로컬 <1s, CI 편차 감안
    start, end, covered_spans, answers = _checkerboard(n_gaps)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    coverage_repo.spans = list(covered_spans)
    _seed_covered_candles(store, covered_spans)
    provider = _FakeProvider(answers)

    t0 = time.perf_counter()
    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    elapsed = time.perf_counter() - t0

    print(f"[DC-16 backfill_job] {n_gaps}개 분리 갭 처리 {elapsed:.4f}s (budget<{budget_sec}s)")
    assert result.gaps_planned == n_gaps
    assert len(provider.calls) == n_gaps  # 갭당 정확히 한 번 -- 재조회/중복fetch 없음
    assert elapsed < budget_sec, (
        f"{n_gaps}개 갭 처리가 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_backfill_job_scales_sub_quadratically_with_gap_count() -> None:
    """`merged` 커버리지 재계산(`merge_spans([*merged, new])`)이 갭마다
    이미 합쳐진 전체 목록을 다시 스캔하므로 이론상 O(갭^2)로 퇴화할 수
    있다. 갭 수를 4배로 늘렸을 때 처리 시간이 넉넉한 여유배수(12배)를
    넘으면 이차 퇴화로 간주해 적색 처리한다(순수 선형이면 ~4배)."""

    async def _time_for(n_gaps: int) -> float:
        start, end, covered_spans, answers = _checkerboard(n_gaps)
        store = _FakeCandleStore()
        coverage_repo = _FakeCoverageRepository()
        coverage_repo.spans = list(covered_spans)
        _seed_covered_candles(store, covered_spans)
        provider = _FakeProvider(answers)
        t0 = time.perf_counter()
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)
        return time.perf_counter() - t0

    small = await _time_for(60)
    large = await _time_for(240)  # 4배

    print(f"[DC-16 backfill_job] scaling: 60 gaps={small:.4f}s, 240 gaps={large:.4f}s")
    ratio_budget = max(small * 12.0, 0.5)
    assert large < ratio_budget, (
        f"gap 4배 증가에 처리시간이 {large / max(small, 1e-6):.1f}배로 늘었습니다"
        f"(이차 퇴화 의심: small={small:.4f}s, large={large:.4f}s, budget<{ratio_budget:.4f}s)."
    )


# ---- D3 -- 다중 인스턴스 동시성 / 고정시드 재생 -------------------------------


@pytest.mark.asyncio
async def test_concurrent_backfills_for_different_instruments_do_not_cross_contaminate() -> None:
    """서로 다른 instrument_id 3개를 같은 `_FakeCandleStore`/
    `_FakeCoverageRepository`/provider 인스턴스에 `asyncio.gather`로
    동시에 백필한다. 이벤트 루프 하나에서 협조적으로 스케줄되는
    코루틴이라 진짜 스레드 경합은 아니지만, 인터리빙된 await 지점 사이에
    다른 워커의 상태가 새는 경우(예: 저장소/커버리지 키를 잘못 공유)는
    이 테스트가 잡는다."""
    start, end = _dt(0), _dt(4)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})

    instrument_ids = [uuid4() for _ in range(3)]
    ulids = [
        "01ARZ3NDEKTSV4RRFFQ69G5FA1",
        "01ARZ3NDEKTSV4RRFFQ69G5FA2",
        "01ARZ3NDEKTSV4RRFFQ69G5FA3",
    ]

    async def _backfill(instrument_id: UUID, ulid: str) -> BackfillJobResult:
        return await _run(
            provider,
            store,
            coverage_repo,
            range_start=start,
            range_end=end,
            listing=_listing(ulid),
            series_key=_series_key(instrument_id=instrument_id),
        )

    results = await asyncio.gather(
        *[_backfill(iid, ulid) for iid, ulid in zip(instrument_ids, ulids, strict=True)]
    )

    for iid, ulid, result in zip(instrument_ids, ulids, results, strict=True):
        assert result.gaps_planned == 1
        assert result.segments[0].stored == 4
        rows = store.rows[(Venue.BITGET, iid, Timeframe.H1)]
        assert {c.open_time for c in rows} == {_dt(0), _dt(1), _dt(2), _dt(3)}
        matching_spans = [s for s in coverage_repo.spans if s.instrument_id == ulid]
        assert len(matching_spans) == 1


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
@pytest.mark.asyncio
async def test_fuzz_fixed_seed_random_coverage_holes_converge_without_overlap(seed: int) -> None:
    """고정시드로 24시간 축 위에 무작위 개수(2~6개)의 기존 커버리지
    조각을 무작위 위치에 흩뿌린 뒤, 한 번의 백필로 남은 모든 갭을 채운다.
    `_FakeCoverageRepository.upsert_span`은 겹치는 구간을 `ValueError`로
    거부하므로, `plan_fetch`가 계산한 갭이 조금이라도 기존 커버리지와
    겹치면 이 테스트가 그 자리에서 적색이 된다. 채운 뒤 재실행하면
    갭이 0이어야 한다(완전 수렴)."""
    rng = random.Random(seed)
    start = _dt(0)
    total_hours = 24
    end = start + timedelta(hours=total_hours)

    n_spans = rng.randint(2, 6)
    used_hours: set[int] = set()
    covered_spans: list[StoredCoverageSpan] = []
    attempts = 0
    while len(covered_spans) < n_spans and attempts < 50:
        attempts += 1
        h = rng.randint(0, total_hours - 2)
        if h in used_hours or (h + 1) in used_hours:
            continue
        used_hours.add(h)
        covered_spans.append(
            StoredCoverageSpan(
                instrument_id=_ULID,
                venue=Venue.BITGET,
                timeframe=Timeframe.H1,
                quality=CoverageQuality.PROVISIONAL,
                start=start + timedelta(hours=h),
                end=start + timedelta(hours=h + 1),
            )
        )

    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    coverage_repo.spans = covered_spans
    _seed_covered_candles(store, covered_spans)

    first = await _run(_EchoProvider(), store, coverage_repo, range_start=start, range_end=end)
    assert first.gaps_planned == len(first.segments)
    for segment in first.segments:
        assert segment.span is not None  # EchoProvider는 항상 응답하므로 빈 세그먼트가 없다

    again = await _run(_EchoProvider(), store, coverage_repo, range_start=start, range_end=end)
    assert again.gaps_planned == 0  # 완전 수렴 -- 남은 갭이 없다
