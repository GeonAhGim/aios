"""task-615(LA-13) DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md#615) 보강.

기존 `test_candle_store.py`는 멱등/스냅샷격리/CHECK위반/격리 4건만 증명해
D1에 머물렀다(축 하한 D3 미달). 이 파일은 그 리프를 새로 만들지 않고(동일
`PostgresCandleStore`/`PostgresBatchRepository`) 부족분만 채운다
(`test_tick_batch_repository_deepen_2987.py`와 동일 원칙 — 500줄 경고
임계 근처인 원 파일을 더 불리지 않고 별도 파일로 보강):

1. failure-injection — `audit_event_id=None`은 §4.1 fail-closed에 따라
   DB에 쓰기 전에 ValueError로 거부됨(fail-fast, 행 0건 증명); `CandleRecord.
   volume`에는 Pydantic 제약이 없어 음수가 애플리케이션 계층을 통과하지만
   `ck_md_candle_volume_nonneg` CHECK가 DB 계층에서 막음(기존 high>=open
   위반 테스트와 다른 CHECK 제약).
2. 수치 성능 단언 — `upsert_batch()`/`batch_repo.get()`이 정확히 상한
   왕복 수 안에서 끝나는지 증명한다. 절대시간은 공유 CI 환경에서 통제할 수
   없는 신호라 게이트로 쓰지 않는다(`test_get_candles.py` round-trip-count
   선례와 동일 원칙).
3. 게이트 적색 재현 — 왕복을 하나 더 내는 어댑터를 끼우면 위 상한 게이트가
   실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라 "작동함이
   증명됨"이어야 한다).
4. 동시성 증명(D3) — 같은 batch_id로 5-way 동시 `create()`를 보내면 정확히
   1건만 성공(task-424/614/2987과 동일 패턴); 서로 다른 batch_id 두 개가
   겹치는 open_time에 동시에 `upsert_batch()`를 걸어도 PK
   `ON CONFLICT DO NOTHING`이 예외 없이 경합을 처리하고 중복 저장이 없는지
   증명한다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    DuplicateBatchError,
    PostgresBatchRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)

_MAX_UPSERT_BATCH_ROUND_TRIPS = 1
_MAX_BATCH_GET_ROUND_TRIPS = 5


@pytest.fixture
def candle_store(pool):
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


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


def _batch(
    *,
    batch_id: uuid.UUID,
    instrument_id: uuid.UUID,
    audit_event_id: uuid.UUID | None,
    range_start: datetime,
    range_end: datetime,
    accepted: int = 1,
    quarantined: int = 0,
    rejected: int = 0,
) -> IngestBatchResult:
    return IngestBatchResult(
        batch_id=batch_id,
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=range_start,
        range_end=range_end,
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT,
            accepted=accepted,
            quarantined=quarantined,
            rejected=rejected,
            issues=[],
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )


async def _create_batch(
    conn: asyncpg.Connection,
    batch_repo: PostgresBatchRepository,
    *,
    instrument_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
    accepted: int = 1,
    quarantined: int = 0,
    rejected: int = 0,
) -> IngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    batch = _batch(
        batch_id=uuid.uuid4(),
        instrument_id=instrument_id,
        audit_event_id=audit_event_id,
        range_start=range_start,
        range_end=range_end,
        accepted=accepted,
        quarantined=quarantined,
        rejected=rejected,
    )
    return await batch_repo.create(conn, batch)


def _query_logger(sink: list[str]):
    def _log(record: object) -> None:
        sink.append(getattr(record, "query", ""))

    return _log


# --- failure-injection -------------------------------------------------


async def test_create_rejects_missing_audit_event_id_before_db_write(pool, batch_repo):
    """failure-injection: `audit_event_id=None`은 §4.1 fail-closed 규칙에
    따라 DB에 쓰기 전에 ValueError로 거부되어야 한다 — md_ingest_batch에
    행이 생기면 안 된다(fail-fast와 실패 후 청소의 구분)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch_id = uuid.uuid4()
        batch = _batch(
            batch_id=batch_id,
            instrument_id=instrument_id,
            audit_event_id=None,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=0,
        )
        with pytest.raises(ValueError, match="audit_event_id"):
            await batch_repo.create(conn, batch)
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch WHERE id = $1", batch_id
        )
    assert row_count == 0, "fail-fast 거부는 DB에 아무 것도 남기면 안 된다"


async def test_upsert_batch_rejects_negative_volume_check_violation(pool, candle_store, batch_repo):
    """negative + failure-injection: `CandleRecord.volume`에는 Pydantic
    제약이 없어(contracts/v1.py) 음수 volume이 애플리케이션 계층을 그대로
    통과한다 — `ck_md_candle_volume_nonneg` CHECK가 DB 계층에서 fail-closed로
    막는지 증명한다(기존 high>=open 위반 테스트와 다른 CHECK 제약)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 110, 90, 105, -1)  # volume(-1) < 0
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_volume_nonneg"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


# --- 수치 성능 단언(round-trip 수 상한) ----------------------------------


@pytest.mark.perf
async def test_upsert_batch_round_trip_count_bounded(pool, candle_store, batch_repo):
    """수치 성능 단언: 200개 캔들을 담은 한 번의 `upsert_batch()` 호출이
    캔들 수와 무관하게 정확히 DB 왕복 1회(단일 multi-row INSERT)만 내는지
    증명한다 — 행별 루프로 회귀하면 이 상한이 깨진다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        row_count = 200
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=row_count),
            accepted=row_count,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candles = [
            _candle(key, t0 + timedelta(minutes=i), 100, 110, 90, 105, 10) for i in range(row_count)
        ]

        queries: list[str] = []
        logger = _query_logger(queries)
        conn.add_query_logger(logger)
        try:
            inserted = await candle_store.upsert_batch(conn, batch.batch_id, candles)
        finally:
            conn.remove_query_logger(logger)

    print(
        f"\nmarket_data upsert_batch({row_count} candles) DB round trips={len(queries)} "
        f"(max={_MAX_UPSERT_BATCH_ROUND_TRIPS})"
    )
    assert inserted == row_count
    assert len(queries) <= _MAX_UPSERT_BATCH_ROUND_TRIPS, (
        f"upsert_batch() DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_UPSERT_BATCH_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


@pytest.mark.perf
async def test_batch_get_round_trip_count_bounded(pool, candle_store, batch_repo):
    """수치 성능 단언: `batch_repo.get()`(내부적으로 메인 행 + verdict
    재계산 2회 + issue 목록 + stored_range로 최대 5회 왕복)이 상한을 넘지
    않는지 증명한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        await candle_store.upsert_batch(
            conn, batch.batch_id, [_candle(key, t0, 100, 110, 90, 105, 10)]
        )

        queries: list[str] = []
        logger = _query_logger(queries)
        conn.add_query_logger(logger)
        try:
            fetched = await batch_repo.get(conn, batch.batch_id, None)
        finally:
            conn.remove_query_logger(logger)

    print(
        f"\nmarket_data batch_repo.get() DB round trips={len(queries)} "
        f"(max={_MAX_BATCH_GET_ROUND_TRIPS})"
    )
    assert fetched is not None
    assert len(queries) <= _MAX_BATCH_GET_ROUND_TRIPS, (
        f"batch_repo.get() DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_BATCH_GET_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


# --- 게이트 적색 재현 -----------------------------------------------------


class _ChattyCandleStore(PostgresCandleStore):
    """게이트 적색 재현(D3): `upsert_batch` 전에 불필요한 왕복을 하나 더
    낸다(`test_get_candles.py` `_ChattyCandleStore`와 동일 기법)."""

    async def upsert_batch(self, conn, batch_id, candles):
        await conn.fetchval("SELECT 1")
        return await super().upsert_batch(conn, batch_id, candles)


@pytest.mark.perf
async def test_upsert_batch_round_trip_gate_detects_extra_query(pool, candle_store, batch_repo):
    """게이트 적색 재현(D3): 왕복을 하나 더 내는 저장소를 끼우면 위 상한(1)
    게이트가 실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라
    "작동함이 증명됨"이어야 한다)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candle = _candle(key, t0, 100, 110, 90, 105, 10)

        chatty = _ChattyCandleStore(pool)
        queries: list[str] = []
        logger = _query_logger(queries)
        conn.add_query_logger(logger)
        try:
            inserted = await chatty.upsert_batch(conn, batch.batch_id, [candle])
        finally:
            conn.remove_query_logger(logger)

    assert inserted == 1
    assert len(queries) == _MAX_UPSERT_BATCH_ROUND_TRIPS + 1
    assert len(queries) > _MAX_UPSERT_BATCH_ROUND_TRIPS


class _ChattyBatchRepository(PostgresBatchRepository):
    """게이트 적색 재현(D3): `get` 전에 불필요한 왕복을 하나 더 낸다
    (`test_tick_batch_repository_deepen_2987.py` `_ChattyBatchRepository`와
    동일 기법, non-tick 경로 적용)."""

    async def get(self, conn, batch_id, tenant_id):
        await conn.fetchval("SELECT 1")
        return await super().get(conn, batch_id, tenant_id)


@pytest.mark.perf
async def test_batch_get_round_trip_gate_detects_extra_query(pool, candle_store, batch_repo):
    """게이트 적색 재현(D3): `batch_repo.get()` 왕복 상한도 chatty 변형으로
    실제 적색이 나는지 증명한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn,
            batch_repo,
            instrument_id=instrument_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        await candle_store.upsert_batch(
            conn, batch.batch_id, [_candle(key, t0, 100, 110, 90, 105, 10)]
        )

    chatty = _ChattyBatchRepository(pool)
    async with pool.acquire() as conn:
        queries: list[str] = []
        logger = _query_logger(queries)
        conn.add_query_logger(logger)
        try:
            fetched = await chatty.get(conn, batch.batch_id, None)
        finally:
            conn.remove_query_logger(logger)

    assert fetched is not None
    assert len(queries) == _MAX_BATCH_GET_ROUND_TRIPS + 1
    assert len(queries) > _MAX_BATCH_GET_ROUND_TRIPS


# --- 동시성 증명 -----------------------------------------------------------


async def test_concurrent_create_same_batch_id_exactly_one_succeeds(pool, batch_repo):
    """동시성 증명(D3): 같은 batch_id로 5-way 동시 `create()`를 보내면
    정확히 1건만 성공하고 나머지는 `DuplicateBatchError`로 거부된다 — PK
    UNIQUE 제약이 경합 상황에서도 fail-closed로 작동함을 증명한다
    (`test_tick_batch_repository_deepen_2987.py`와 동일 패턴, non-tick
    경로 적용)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    batch_id = uuid.uuid4()

    async def _attempt() -> str:
        async with pool.acquire() as conn, conn.transaction():
            audit_event_id = await _audit_event_id(conn)
            batch = _batch(
                batch_id=batch_id,
                instrument_id=instrument_id,
                audit_event_id=audit_event_id,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
            )
            try:
                await batch_repo.create(conn, batch)
                return "success"
            except DuplicateBatchError:
                return "duplicate"

    results = await asyncio.gather(*[_attempt() for _ in range(5)])

    assert results.count("success") == 1, (
        f"동시 삽입 5건 중 성공이 정확히 1건이어야 하는데 {results.count('success')}건"
    )
    assert results.count("duplicate") == 4, (
        f"나머지 4건이 DuplicateBatchError로 거부돼야 하는데 {results.count('duplicate')}건만 거부"
    )


async def test_concurrent_upsert_batch_overlapping_candles_no_duplicate_no_crash(
    pool, candle_store, batch_repo
):
    """동시성 증명(D3): 서로 다른 batch_id 두 개가 같은 시계열의 겹치는
    open_time 구간에 동시에 `upsert_batch()`를 걸어도(재수집 레이스) PK
    `ON CONFLICT DO NOTHING`이 유니크 위반 예외 없이 경합을 처리하고,
    최종 저장 행 수가 고유 open_time 수와 정확히 같은지 증명한다(중복 저장도
    크래시도 없어야 한다)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    open_times = [t0 + timedelta(minutes=i) for i in range(5)]
    candles = [_candle(key, ot, 100, 110, 90, 105, 10) for ot in open_times]

    async def _attempt() -> int:
        async with pool.acquire() as conn, conn.transaction():
            batch = await _create_batch(
                conn,
                batch_repo,
                instrument_id=instrument_id,
                range_start=t0,
                range_end=open_times[-1] + timedelta(minutes=1),
                accepted=len(candles),
            )
            return await candle_store.upsert_batch(conn, batch.batch_id, candles)

    inserted_counts = await asyncio.gather(_attempt(), _attempt())

    assert sum(inserted_counts) == len(open_times), (
        "겹치는 open_time에 대한 동시 upsert_batch 두 건의 삽입 합이 "
        f"고유 캔들 수({len(open_times)})와 같아야 하는데 {inserted_counts}"
    )
    async with pool.acquire() as conn, conn.transaction():
        stored = await candle_store.query(
            conn,
            key,
            t0 - timedelta(minutes=1),
            open_times[-1] + timedelta(minutes=1),
            as_of=None,
        )
    assert [c.open_time for c in stored] == open_times, "중복 저장 없이 고유 행만 남아야 한다"
