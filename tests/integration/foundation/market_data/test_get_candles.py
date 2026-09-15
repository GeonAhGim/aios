"""LA-17 `application/get_candles.get_candles` 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-17.
DoD(task-624): 조정계수는 as_of 시점 기준으로만 반영, 갭은 정보로만 반환(비
strict), negative: 미등록 instrument → 명시적 에러.

BITGET(연속 세션)만 쓴다 — `VenueCalendar`(LA-3) 휴장일 적재 없이도 세션
판정이 가능해 캘린더 시드가 필요 없다(get_candles.py의 `_sessions_for_range`
가 연속 venue는 `CalendarRepository`를 아예 호출하지 않는다).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.application.get_candles import (
    AsOfInFutureError,
    QuarantinedViewUnsupportedError,
    UnknownSeriesError,
    get_candles,
)
from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    CandleRecord,
    CorporateAction,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)
from tests.integration.foundation.market_data.perf_replay_support import (
    DAY_ROW_COUNT,
    new_instrument_id,
    seed_candles,
    series_key,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


@pytest.fixture
def reference_repo(pool):
    return PostgresReferenceRepository(pool)


@pytest.fixture
def calendar_repo(pool):
    return PostgresCalendarRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )


def _candle(
    key: SeriesKey, open_time: datetime, o: float, h: float, low: float, c: float, v: float
) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


async def _seed_candle(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id, open_time
) -> SeriesKey:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    audit_event_id = await _audit_event_id(conn)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=open_time,
        range_end=open_time + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(
        conn, batch.batch_id, [_candle(key, open_time, 100, 110, 90, 105, 10)]
    )
    return key


async def test_get_candles_returns_raw_series_with_hash_and_no_gaps(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert [c.open_time for c in series.candles] == [t0]
    assert series.gaps == []
    assert series.adjustment is Adjustment.RAW
    assert len(series.series_hash) == 64


async def test_get_candles_reports_gap_without_raising(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    # t0+1분은 결측 — 3분짜리 창 안에 캔들 1개(t0)만 있으므로 갭 1구간.
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=3))
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.gaps == [(t0 + timedelta(minutes=1), t0 + timedelta(minutes=3))]


async def test_get_candles_adjustment_ignores_action_after_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `ex_date`가 `as_of`보다 미래인 조정은 `factor_chain`(LA-8)이
    제외한다 — as_of 시점에는 아직 일어나지 않은 조정이므로."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정 — md_candle 파티션은 현재~+N개월만 존재하므로(과거 달
        # 파티션 없음) 이번 달 안에서만 "과거" 캔들을 만든다.
        t0 = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )
        await reference_repo.record_action(
            conn,
            CorporateAction(
                action_type="SPLIT",
                instrument_id=instrument_id,
                ex_date=as_of.date() + timedelta(days=1),
                ratio=Decimal(2),
                source_ref="test:future",
            ),
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("100"), "as_of보다 미래인 ex_date는 미반영"


async def test_get_candles_adjustment_applies_action_before_as_of(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """`ex_date`가 `as_of` 이전(<=)이고 캔들 날짜보다는 이후면 조정이 반영된다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        as_of = (await conn.fetchval("SELECT now()")).astimezone(timezone.utc)
        # 월초로 고정(위 테스트와 같은 이유) — ex_date는 캔들(월초)보다는
        # 늦고 as_of(오늘)보다는 이르거나 같아야 하므로 오늘 날짜를 쓴다.
        t0 = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )
        await reference_repo.record_action(
            conn,
            CorporateAction(
                action_type="SPLIT",
                instrument_id=instrument_id,
                ex_date=as_of.date(),
                ratio=Decimal(2),
                source_ref="test:past",
            ),
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        as_of=as_of,
        adjustment=Adjustment.ADJUSTED,
    )
    series = await get_candles(
        query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )
    assert series.candles[0].open == Decimal("50"), "as_of 이전 ex_date 조정은 반영되어야 한다"
    assert series.candles[0].volume == Decimal("20")


async def test_get_candles_unknown_series_raises(pool, candle_store, reference_repo, calendar_repo):
    """negative: 한 번도 수집된 적 없는 (venue, instrument, timeframe) → 명시적 에러."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    with pytest.raises(UnknownSeriesError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


async def test_get_candles_as_of_in_future_raises(
    pool, candle_store, reference_repo, calendar_repo
):
    """negative: `as_of`가 현재보다 미래면 조회 전에 즉시 거부한다(스토어를
    건드리지 않으므로 미등록 instrument여도 무방)."""
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid.uuid4(), timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    future_as_of = t0 + timedelta(days=1)
    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=future_as_of)
    with pytest.raises(AsOfInFutureError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


async def test_get_candles_quarantined_view_unsupported_raises(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """negative: `CandleStore.query`(LA-13)가 지원하지 않는 파라미터는 조용히
    무시하지 않고 명시적으로 거부한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, candle_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(
        key=key,
        start=t0,
        end=t0 + timedelta(minutes=1),
        include_quarantined=True,
    )
    with pytest.raises(QuarantinedViewUnsupportedError):
        await get_candles(
            query, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


# ---------- 실패주입(D3) — `read_candles_columnar`가 손상된 데이터를 돌려주는 경우 ----------


class _CorruptedColumnsCandleStore(PostgresCandleStore):
    """`read_candles_columnar`(LA-23b)가 배열 길이가 서로 다른 `CandleColumns`를
    돌려주는 실제 어댑터 결함을 시뮬레이션한다(예: 컬럼 하나를 별도 조회로
    바꾸다 WHERE 필터가 어긋나는 리팩터링 회귀). `to_candle_records`(domain/
    candle_columns.py)는 인덱스로만 각 배열을 짝짓기 때문에, 길이가 다르면
    조용히 잘못 정렬된 OHLCV를 만들어낼 수 있다 — `get_candles`가 이 결함을
    삼키지 않고 그대로 전파해 fail-closed 하는지 증명한다."""

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: datetime,
        end: datetime,
        as_of: datetime | None,
    ) -> CandleColumns:
        columns = await super().read_candles_columnar(conn, key, start, end, as_of)
        return CandleColumns(
            ts=columns.ts,
            open=columns.open[:-1],
            high=columns.high,
            low=columns.low,
            close=columns.close,
            volume=columns.volume,
            quote_volume=columns.quote_volume,
        )


async def test_get_candles_raises_when_store_returns_mismatched_columns(
    pool, batch_repo, reference_repo, calendar_repo
):
    """실패주입: 저장소가 배열 길이가 어긋난 컬럼을 돌려주면 `get_candles`는
    잘못 정렬된 캔들을 조용히 만들지 않고 `MismatchedColumnLengthError`를
    그대로 전파해야 한다(fail-closed)."""
    corrupted_store = _CorruptedColumnsCandleStore(pool)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        key = await _seed_candle(
            conn, batch_repo, corrupted_store, instrument_id=instrument_id, open_time=t0
        )

    query = CandleQuery(key=key, start=t0, end=t0 + timedelta(minutes=1))
    with pytest.raises(MismatchedColumnLengthError):
        await get_candles(
            query, store=corrupted_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )


# ---------- 수치 성능 단언(D3) — `get_candles` 순차 DB 왕복 수(구조 회귀 가드) ----------
#
# `tests/integration/foundation/market_data/test_perf_replay.py`(task-1081/LA-23b)와
# 같은 근거·같은 기법이다: RAW 조회는 `CandleStore.last_open_time`(시계열
# 존재 확인) + `read_candles_columnar`(컬럼지향 읽기) 2회로 고정이며, 행
# 수와 무관한 구조 상수다(`Venue.BITGET`은 CONTINUOUS라 세션 계산에 DB
# 조회가 없다). 그 파일은 `replay()`만 재던 이 상수를 `get_candles()`에는
# 아직 증명하지 않았다 — 이 리프의 부족분(수치 성능 단언 없음)을 채운다.

_MAX_GET_CANDLES_ROUND_TRIPS = 2


class _PinnedConnectionPool:
    """`get_candles(pool=...)`에 넘기는 풀 대역 — `acquire()`가 항상 미리
    얻어 둔 커넥션 하나를 돌려준다(반납하지 않는다). 워밍업과 계수가 같은
    커넥션에서 일어나야 asyncpg의 1회성 코덱 조회가 계수에 섞이지 않는다
    (`perf_replay_support.py`의 `_PinnedConnectionPool`과 동일 이유 — 그
    모듈은 이 클래스를 공개하지 않으므로 여기서 별도로 둔다)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


async def _count_get_candles_round_trips(
    pool: asyncpg.Pool, query: CandleQuery, *, store, refs, cal
) -> int:
    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        pinned = _PinnedConnectionPool(conn)
        await get_candles(query, store=store, refs=refs, cal=cal, pool=pinned)

        conn.add_query_logger(_log)
        try:
            await get_candles(query, store=store, refs=refs, cal=cal, pool=pinned)
        finally:
            conn.remove_query_logger(_log)

    return len(queries)


@pytest.mark.perf
async def test_get_candles_round_trip_count_bounded(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    """수치 성능 단언: RAW 조회 1회가 내는 순차 DB 왕복 수가 상한(2)을
    넘지 않는지 1일(1,440행) 규모로 증명한다. 절대시간은 공유 CI 환경에서
    통제할 수 없는 신호라 게이트로 쓰지 않는다(print만, task-1405/
    test_perf_replay.py 선례와 동일 원칙)."""
    async with pool.acquire() as conn:
        instrument_id = await new_instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    await seed_candles(
        pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=DAY_ROW_COUNT
    )

    query = CandleQuery(
        key=series_key(instrument_id), start=t0, end=t0 + timedelta(minutes=DAY_ROW_COUNT)
    )
    round_trip_count = await _count_get_candles_round_trips(
        pool, query, store=candle_store, refs=reference_repo, cal=calendar_repo
    )
    print(
        f"\nmarket_data get_candles sequential DB round trips={round_trip_count} "
        f"(max={_MAX_GET_CANDLES_ROUND_TRIPS}, rows={DAY_ROW_COUNT})"
    )
    assert round_trip_count <= _MAX_GET_CANDLES_ROUND_TRIPS, (
        f"get_candles() 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_GET_CANDLES_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


class _ChattyCandleStore(PostgresCandleStore):
    """게이트 적색 재현/실패주입 — 시계열 존재 확인 전에 불필요한 왕복을
    하나 더 낸다(행별 쿼리를 끼워 넣는 회귀의 최소 재현, test_perf_replay.py
    `_ChattyCandleStore`와 동일 기법)."""

    async def last_open_time(self, conn: asyncpg.Connection, key: SeriesKey):
        await conn.fetchval("SELECT 1")
        return await super().last_open_time(conn, key)


@pytest.mark.perf
async def test_get_candles_round_trip_gate_detects_extra_query(
    pool, batch_repo, reference_repo, calendar_repo
):
    """게이트 적색 재현(D3): 왕복을 하나 더 내는 저장소를 끼우면 위 상한(2)
    게이트가 실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라
    "작동함이 증명됨"이어야 한다)."""
    async with pool.acquire() as conn:
        instrument_id = await new_instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    await seed_candles(pool, batch_repo, instrument_id=instrument_id, t0=t0, row_count=10)

    query = CandleQuery(key=series_key(instrument_id), start=t0, end=t0 + timedelta(minutes=10))
    round_trip_count = await _count_get_candles_round_trips(
        pool, query, store=_ChattyCandleStore(pool), refs=reference_repo, cal=calendar_repo
    )
    assert round_trip_count == _MAX_GET_CANDLES_ROUND_TRIPS + 1
    assert round_trip_count > _MAX_GET_CANDLES_ROUND_TRIPS
