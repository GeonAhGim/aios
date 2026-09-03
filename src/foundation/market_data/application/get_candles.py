"""LA-17 — 캔들 조회. `as_of` 이전에 저장된 배치만, RAW|ADJUSTED 선택 조회.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-17.

이 모듈은 재구현하지 않는다 — 배치 해시는 `domain/lineage.batch_hash`(LA-8),
조정계수는 `domain/corporate_actions/adjustment`(LA-8), 갭 판정은
`domain/quality/gap_detector.detect_gaps`(LA-5) + `VenueCalendar`(LA-3)에
그대로 위임한다. 캔들 데이터 읽기는 `ports/candle_store.CandleStore`(LA-13
어댑터)로만 하고, 이 리프에서 SQL을 새로 작성하지 않는다.

**미검증/제약**: `ReferenceRepository`(LA-9/12)에는 `instrument_id`만으로
인스트루먼트 존재를 확인하는 조회가 없다(있는 것은 canonical 심볼로 찾는
`get_instrument`뿐). 그래서 "미등록 instrument"는
`CandleStore.last_open_time`이 `None`인 것으로 판정한다 — 이는 "등록된 적
없음"과 "등록은 됐지만 아직 한 번도 수집되지 않음"을 구분하지 못하는
근사치다. 두 경우 모두 호출자 조치는 같다(참조데이터 확인 후 수집을 먼저
시켜야 한다)는 점에서 fail-closed 신호로는 충분하다고 본다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import asyncpg

from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    CandleRecord,
    CandleSeries,
    QualityIssue,
    SeriesKey,
    SessionWindow,
    Venue,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import SessionSpec, VenueCalendar
from src.foundation.market_data.domain.candle_columns import to_candle_records
from src.foundation.market_data.domain.corporate_actions.adjustment import adjust, factor_chain
from src.foundation.market_data.domain.lineage import batch_hash
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import duration, expected_opens
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = [
    "AsOfInFutureError",
    "QuarantinedViewUnsupportedError",
    "UnknownSeriesError",
    "coalesce_gaps",
    "ensure_as_of_not_future",
    "get_candles",
    "load_series",
]


class AsOfInFutureError(ValueError):
    """`MD_AS_OF_IN_FUTURE` — `as_of`가 현재 시각보다 미래다(불가, 요청 수정)."""

    def __init__(self, as_of: datetime, now: datetime) -> None:
        super().__init__(f"as_of={as_of.isoformat()}가 현재({now.isoformat()})보다 미래입니다.")


class UnknownSeriesError(Exception):
    """`MD_SYMBOL_UNKNOWN` — 이 (venue, instrument, timeframe)로 저장된 캔들이
    한 번도 없다(모듈 docstring의 근사치 제약 참고)."""

    def __init__(self, key: SeriesKey) -> None:
        super().__init__(
            f"등록되지 않았거나 수집된 적 없는 시계열: venue={key.venue.value} "
            f"instrument_id={key.instrument_id} timeframe={key.timeframe.value}"
        )
        self.key = key


class QuarantinedViewUnsupportedError(Exception):
    """`CandleQuery.include_quarantined=True`는 `CandleStore.query`(LA-13)가
    지원하지 않는다 — 조용히 무시하지 않고 명시적으로 거부한다(fail-closed)."""


async def _ensure_known_series(
    conn: asyncpg.Connection, store: CandleStore, key: SeriesKey
) -> None:
    if await store.last_open_time(conn, key) is None:
        raise UnknownSeriesError(key)


def ensure_as_of_not_future(as_of: datetime, now: datetime) -> None:
    if as_of > now:
        raise AsOfInFutureError(as_of, now)


def _effective_as_of(as_of: datetime | None, now: datetime) -> datetime:
    if as_of is None:
        return now
    ensure_as_of_not_future(as_of, now)
    return as_of


async def _sessions_for_range(
    conn: asyncpg.Connection,
    cal: CalendarRepository,
    venue: Venue,
    start: datetime,
    end: datetime,
) -> list[SessionWindow]:
    spec: SessionSpec = KNOWN_SESSIONS[venue.value]
    if spec.continuous:
        calendar = VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)
        return _collect_sessions(calendar, start, end, spec)

    calendars_by_year: dict[int, VenueCalendar] = {}
    sessions: list[SessionWindow] = []
    day = start.astimezone(spec.tz).date()
    end_day = end.astimezone(spec.tz).date()
    while day <= end_day:
        if day.year not in calendars_by_year:
            calendars_by_year[day.year] = await cal.load(conn, venue, day.year)
        sessions.extend(calendars_by_year[day.year].sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _collect_sessions(
    calendar: VenueCalendar, start: datetime, end: datetime, spec: SessionSpec
) -> list[SessionWindow]:
    sessions: list[SessionWindow] = []
    day: date = start.astimezone(spec.tz).date()
    end_day = end.astimezone(spec.tz).date()
    while day <= end_day:
        sessions.extend(calendar.sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _clip_sessions(
    sessions: list[SessionWindow], start: datetime, end: datetime
) -> list[SessionWindow]:
    """`detect_gaps`(LA-5)는 넘겨받은 세션 전체(`min(open_at)~max(close_at)`)를
    기대 구간으로 삼는다 — 하루 전체 세션을 그대로 넘기면 요청한 `[start,
    end)`보다 훨씬 넓게 갭을 판정해버린다. 그래서 세션을 요청 구간과
    교집합으로 잘라 넘긴다(세션 밖은 여전히 갭이 아니라는 `detect_gaps`의
    규칙은 그대로 유지된다)."""
    clipped: list[SessionWindow] = []
    for session in sessions:
        clipped_open = max(session.open_at, start)
        clipped_close = min(session.close_at, end)
        if clipped_open < clipped_close:
            clipped.append(
                SessionWindow(open_at=clipped_open, close_at=clipped_close, kind=session.kind)
            )
    return clipped


def coalesce_gaps(
    missing_opens: list[datetime], step: timedelta
) -> list[tuple[datetime, datetime]]:
    """연속(간격이 정확히 `step`)된 결측 open_time을 `[start, end)` 구간으로
    묶는다. `CandleSeries.gaps`는 개별 시각이 아니라 구간 목록이다."""
    if not missing_opens:
        return []
    ordered = sorted(missing_opens)
    ranges: list[tuple[datetime, datetime]] = []
    run_start = ordered[0]
    prev = ordered[0]
    for ot in ordered[1:]:
        if ot - prev == step:
            prev = ot
            continue
        ranges.append((run_start, prev + step))
        run_start = ot
        prev = ot
    ranges.append((run_start, prev + step))
    return ranges


async def load_series(
    q: CandleQuery,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    conn: asyncpg.Connection,
    now: datetime,
) -> tuple[list[CandleRecord], list[QualityIssue], int]:
    """조회 → (필요 시) 조정 → 갭 판정까지의 공통 코어. `get_candles`와
    `replay_candles`(LA-17 같은 리프)가 이 함수 하나로 로직을 공유한다 —
    strict 여부(리플레이의 예외 발생)만 호출자가 다르게 처리한다.

    반환: (캔들, 갭 이슈 목록, 기대 open_time 총 개수)."""
    if q.include_quarantined:
        raise QuarantinedViewUnsupportedError()

    key = q.key
    await _ensure_known_series(conn, store, key)

    # LA-23b(ADR-2026-09-04-A #1): 대량 소비자(리플레이·이 함수의 갭 판정)는
    # `query()`의 레코드별 pydantic 검증 대신 컬럼지향 경로로 읽는다 —
    # `to_candle_records`가 `model_construct`로 재구성하므로 결과 값은
    # `query()`와 동일하다(ohlc_sanity가 쓰기 시점에 이미 강제한 불변식).
    columns = await store.read_candles_columnar(conn, key, q.start, q.end, q.as_of)
    candles = to_candle_records(columns, key)

    if q.adjustment is Adjustment.ADJUSTED:
        as_of_for_factors = q.as_of if q.as_of is not None else now
        actions = await refs.list_actions(conn, key.instrument_id)
        factors = factor_chain(actions, as_of_for_factors)
        candles = adjust(candles, factors)

    raw_sessions = await _sessions_for_range(conn, cal, key.venue, q.start, q.end)
    sessions = _clip_sessions(raw_sessions, q.start, q.end)
    issues = detect_gaps(candles, key.timeframe, sessions)
    expected_total = len(expected_opens(q.start, q.end, key.timeframe, sessions))
    return candles, issues, expected_total


async def get_candles(
    q: CandleQuery,
    *,
    store: CandleStore,
    refs: ReferenceRepository,
    cal: CalendarRepository,
    pool: asyncpg.Pool,
) -> CandleSeries:
    """§9.2 LA-17: `as_of` 이전에 저장된 배치만 조회, `adjustment`에 따라
    RAW|ADJUSTED. 갭은 정보로만 반환한다(strict 예외는 `replay_candles`
    소관)."""
    now = datetime.now(timezone.utc)
    effective_as_of = _effective_as_of(q.as_of, now)

    async with pool.acquire() as conn:
        candles, issues, _expected_total = await load_series(
            q, store=store, refs=refs, cal=cal, conn=conn, now=now
        )

    step = duration(q.key.timeframe)
    gaps = coalesce_gaps([i.open_time for i in issues if i.open_time is not None], step)

    return CandleSeries(
        key=q.key,
        candles=candles,
        gaps=gaps,
        adjustment=q.adjustment,
        as_of=effective_as_of,
        series_hash=batch_hash(candles),
    )
