"""PostgresCandleStore/PostgresBatchRepository 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-13.
DoD(task-615): ON CONFLICT DO NOTHING(재수집 멱등) + 파티션 경계 넘는 삽입
정상 + as_of 조회 스냅샷 격리, negative: CHECK 위반 캔들 삽입 거부.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

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
    QualityIssue,
    QualityIssueType,
    QualityVerdict,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
    Verdict,
)


@pytest.fixture
def candle_store(pool: asyncpg.Pool[asyncpg.Connection]) -> PostgresCandleStore:
    return PostgresCandleStore(pool)


@pytest.fixture
def batch_repo(pool: asyncpg.Pool[asyncpg.Connection]) -> PostgresBatchRepository:
    return PostgresBatchRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    result = await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )
    return cast(uuid.UUID, result)


async def _instrument_id(conn: asyncpg.Connection) -> uuid.UUID:
    symbol = f"TEST-{uuid.uuid4().hex}"
    result = await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
        "RETURNING instrument_id",
        symbol,
    )
    return cast(uuid.UUID, result)


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
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
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
    return await batch_repo.create(conn, batch)


async def test_upsert_batch_is_idempotent_on_reingest(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=5), accepted=2,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candles = [
            _candle(key, t0, 100, 110, 90, 105, 10),
            _candle(key, t0 + timedelta(minutes=1), 105, 115, 95, 110, 12),
        ]
        first = await candle_store.upsert_batch(conn, batch.batch_id, candles)
        second = await candle_store.upsert_batch(conn, batch.batch_id, candles)
    assert first == 2
    assert second == 0, "재수집(같은 캔들 재삽입)은 멱등하게 0건이어야 한다"
    async with pool.acquire() as conn, conn.transaction():
        stored = await candle_store.query(
            conn, key, t0 - timedelta(minutes=1), t0 + timedelta(minutes=10), as_of=None
        )
    assert len(stored) == 2


async def test_upsert_batch_across_partition_boundary(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT md_ensure_partitions(6)")
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        t1 = t0 + timedelta(days=40)  # 어떤 시작일이든 최소 한 달 경계는 넘는다
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t1 + timedelta(minutes=1), accepted=2,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candles = [
            _candle(key, t0, 100, 110, 90, 105, 10),
            _candle(key, t1, 200, 210, 190, 205, 20),
        ]
        inserted = await candle_store.upsert_batch(conn, batch.batch_id, candles)
    assert inserted == 2
    async with pool.acquire() as conn, conn.transaction():
        stored = await candle_store.query(
            conn, key, t0 - timedelta(minutes=1), t1 + timedelta(minutes=1), as_of=None
        )
    assert [c.open_time for c in stored] == [t0, t1]


async def test_query_as_of_snapshot_isolation_ignores_later_insert(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    t1 = t0 + timedelta(minutes=1)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        batch_a = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t1 + timedelta(minutes=1),
        )
        candle_a = _candle(key, t0, 100, 110, 90, 105, 10)
        await candle_store.upsert_batch(conn, batch_a.batch_id, [candle_a])
        as_of = await conn.fetchval("SELECT now()")
    async with pool.acquire() as conn, conn.transaction():
        batch_b = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t1, range_end=t1 + timedelta(minutes=1),
        )
        candle_b = _candle(key, t1, 200, 210, 190, 205, 20)
        await candle_store.upsert_batch(conn, batch_b.batch_id, [candle_b])
    async with pool.acquire() as conn, conn.transaction():
        snapshot = await candle_store.query(
            conn, key, t0 - timedelta(minutes=1), t1 + timedelta(minutes=1), as_of=as_of
        )
        latest = await candle_store.query(
            conn, key, t0 - timedelta(minutes=1), t1 + timedelta(minutes=1), as_of=None
        )

    assert [c.open_time for c in snapshot] == [t0], "as_of 시점 이후 삽입은 스냅샷에 보이면 안 된다"
    assert [c.open_time for c in latest] == [t0, t1]


async def test_upsert_batch_rejects_ohlc_check_violation(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: high < open인 캔들은 md_candle의 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 90, 80, 85, 10)  # high(90) < open(100)
    # CHECK 위반은 트랜잭션을 abort 상태로 만들므로(위 duplicate 테스트와 같은
    # 이유) 별도 트랜잭션에서 시도한다.
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_high_ge_open"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_quarantine_writes_only_quarantine_table(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1), accepted=0, quarantined=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candle = _candle(key, t0, 100, 110, 90, 105, -1)  # 원 소스가 음수 volume을 보냈다고 가정
        issue = QualityIssue(
            type=QualityIssueType.NEGATIVE_VOLUME,
            severity=Severity.REJECT,
            open_time=t0,
            detail={"volume": "-1"},
        )
        await candle_store.quarantine(conn, batch.batch_id, [candle], [issue])
        quarantine_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle WHERE batch_id = $1", batch.batch_id
        )
        assert quarantine_count == 1
        last = await candle_store.last_open_time(conn, key)
    assert last is None, "격리 캔들은 md_candle에 없으므로 last_open_time에 영향을 주면 안 된다"


async def test_last_open_time_tracks_latest_stored_candle(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        empty = await candle_store.last_open_time(conn, key)
        assert empty is None

        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        t1 = t0 + timedelta(minutes=1)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t1 + timedelta(minutes=1), accepted=2,
        )
        await candle_store.upsert_batch(
            conn, batch.batch_id,
            [_candle(key, t0, 100, 110, 90, 105, 10), _candle(key, t1, 105, 115, 95, 110, 12)],
        )
        latest = await candle_store.last_open_time(conn, key)
    assert latest == t1


async def test_batch_create_then_get_reconstructs_verdict_from_stored_candles(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1),
            accepted=1, quarantined=0, rejected=1,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        candle = _candle(key, t0, 100, 110, 90, 105, 10)
        await candle_store.upsert_batch(conn, batch.batch_id, [candle])
        fetched = await batch_repo.get(conn, batch.batch_id, None)
    assert fetched is not None
    assert fetched.verdict.accepted == 1
    assert fetched.verdict.quarantined == 0
    assert fetched.verdict.rejected == 1
    assert fetched.stored_range == (t0, t0)


async def test_batch_create_duplicate_batch_id_raises(
    pool: asyncpg.Pool[asyncpg.Connection], batch_repo: PostgresBatchRepository
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1), accepted=0,
        )

    # PK 위반은 트랜잭션을 abort 상태로 만들어 그 안의 이후 COMMIT을 깨뜨리므로
    # (test_reference_repository.py의 같은 패턴), 실패할 create()는 별도 트랜잭션으로.
    with pytest.raises(DuplicateBatchError):
        async with pool.acquire() as conn, conn.transaction():
            await batch_repo.create(conn, batch)


async def test_upsert_batch_rejects_low_below_high_check_violation(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: low > high인 캔들은 md_candle의 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 120, 125, 110, 10)  # low(125) > high(120)
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_high_ge_low"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_upsert_batch_rejects_close_outside_range_check_violation(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: close가 high와 low 사이에 없으면 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 120, 90, 130, 10)  # close(130) > high(120)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_upsert_batch_rejects_negative_volume(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: 음수 거래량은 CHECK 위반으로 거부되어야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=1),
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)

    bad_candle = _candle(key, t0, 100, 110, 90, 105, -5)  # 음수 거래량
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_volume"):
        async with pool.acquire() as conn, conn.transaction():
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


async def test_query_with_empty_time_range(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """negative: start > end인 시간 범위 조회는 빈 결과를 반환해야 한다."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    t1 = t0 + timedelta(minutes=5)

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t1 + timedelta(minutes=1),
        )
        candle = _candle(key, t0, 100, 110, 90, 105, 10)
        await candle_store.upsert_batch(conn, batch.batch_id, [candle])

    async with pool.acquire() as conn, conn.transaction():
        # start > end인 범위로 조회
        result = await candle_store.query(
            conn, key, t1, t0, as_of=None  # reversed time range
        )
    assert len(result) == 0, "반전된 시간 범위는 빈 결과를 반환해야 한다"


async def test_batch_repo_handles_verdict_transitions(
    pool: asyncpg.Pool[asyncpg.Connection], batch_repo: PostgresBatchRepository
) -> None:
    """failure-injection: 배치 생성 중 verdict 상태 전이를 검증하고 저장한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        audit_event_id = await _audit_event_id(conn)

        # PARTIAL verdict로 배치 생성
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(),
            source="test_source",
            venue=Venue.BITGET,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1,
            range_start=t0,
            range_end=t0 + timedelta(minutes=5),
            request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(
                verdict=Verdict.PARTIAL,
                accepted=8,
                quarantined=1,
                rejected=1,
                issues=[],
            ),
            batch_hash=f"hash-{uuid.uuid4().hex}",
            audit_event_id=audit_event_id,
            stored_range=None,
        )
        result = await batch_repo.create(conn, batch)
        assert result.verdict.verdict == Verdict.PARTIAL
        assert result.verdict.accepted == 8
        assert result.verdict.quarantined == 1
        assert result.verdict.rejected == 1


async def test_quarantine_with_multiple_candles(
    pool: asyncpg.Pool[asyncpg.Connection],
    candle_store: PostgresCandleStore,
    batch_repo: PostgresBatchRepository,
) -> None:
    """failure-injection: 격리 시 여러 캔들을 정확히 기록하는지 확인."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        batch = await _create_batch(
            conn, batch_repo, instrument_id=instrument_id,
            range_start=t0, range_end=t0 + timedelta(minutes=3), accepted=0, quarantined=3,
        )
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        
        candles = [
            _candle(key, t0 + timedelta(minutes=i), 100, 110, 90, 105, 10)
            for i in range(3)
        ]
        
        issues = [
            QualityIssue(
                type=QualityIssueType.OHLC_INCONSISTENT,
                severity=Severity.REJECT,
                open_time=t0,
                detail={"reason": "close > high"},
            ),
        ]
        await candle_store.quarantine(conn, batch.batch_id, candles, issues)
        
        # 모든 격리 캔들이 저장되었는지 확인
        quarantine_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_quarantine_candle WHERE batch_id = $1", batch.batch_id
        )
        assert quarantine_count == 3, "격리된 캔들 3건이 모두 저장되어야 한다"


async def test_last_open_time_on_empty_table(
    pool: asyncpg.Pool[asyncpg.Connection], candle_store: PostgresCandleStore
) -> None:
    """negative: 저장된 캔들이 없으면 last_open_time은 None을 반환해야 한다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        result = await candle_store.last_open_time(conn, key)
        assert result is None, "저장된 캔들이 없는 경우 None을 반환해야 한다"
