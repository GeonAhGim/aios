"""task-656(LA-16a) DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md) 보강.

기존 `test_tick_batch_repository.py`는 happy path 1건 + negative 2건(PK
위반, 교차tenant 404 동형)만 증명해 D1에 머물렀다(축 하한 D3 미달). 이
파일은 그 리프를 새로 만들지 않고(동일 `PostgresBatchRepository`,
`TickIngestBatchResult`) 부족분만 채운다:

1. negative 1건 추가 + failure-injection — `QualityVerdict.accepted`(contracts/
   v1.py)에는 음수를 막는 필드 제약이 없어 오염된 값이 애플리케이션 계층을
   그대로 통과한다. `md_ingest_batch_tick`의
   `ck_md_ingest_batch_tick_counts_nonneg` CHECK가 DB 계층에서 fail-closed로
   막는지 증명한다(§4.1).
2. 수치 성능 단언 — create_tick_batch/get_tick_batch가 각각 정확히 DB 왕복
   1회(INSERT 1회, SELECT 1회)만 내는지 증명한다. 절대시간은 공유 CI
   환경에서 통제할 수 없는 신호라 게이트로 쓰지 않는다
   (`test_get_candles.py` round-trip-count 선례와 동일 원칙).
3. 게이트 적색 재현 — 왕복을 하나 더 내는 저장소를 끼우면 위 상한 게이트가
   실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라 "작동함이
   증명됨"이어야 한다).
4. 동시성 증명(D3) — 같은 batch_id로 5-way 동시 create_tick_batch를 보내면
   정확히 1건만 성공하고 나머지는 DuplicateBatchError로 거부되는지 증명한다
   (task-424/614와 동일 패턴).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    DuplicateBatchError,
    PostgresBatchRepository,
)
from src.foundation.market_data.contracts.v1 import (
    QualityVerdict,
    TickIngestBatchResult,
    Venue,
    Verdict,
)

_MAX_CREATE_ROUND_TRIPS = 1
_MAX_GET_ROUND_TRIPS = 1


@pytest.fixture
def batch_repo(pool):
    return PostgresBatchRepository(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest_tick', 'SUCCESS', "
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


def _tick_batch(
    *,
    instrument_id: uuid.UUID,
    audit_event_id: uuid.UUID,
    range_start: datetime,
    range_end: datetime,
    accepted: int = 1,
    quarantined: int = 0,
    rejected: int = 0,
) -> TickIngestBatchResult:
    return TickIngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=None,
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
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
    )


def _query_logger(sink: list[str]):
    def _log(record: object) -> None:
        sink.append(getattr(record, "query", ""))

    return _log


async def test_create_tick_batch_negative_accepted_count_rejected_by_db_check(pool, batch_repo):
    """negative + failure-injection(D2): 애플리케이션(Pydantic) 계층은
    `accepted=-1`을 그대로 통과시키지만, `ck_md_ingest_batch_tick_counts_nonneg`
    CHECK 제약이 DB 계층에서 막는다. PK 위반과 같은 이유로 abort된 트랜잭션
    안 이후 COMMIT을 깨뜨리므로 전체를 `pytest.raises`로 감싼다(같은 파일
    `test_create_tick_batch_duplicate_batch_id_raises`와 동일 패턴)."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        async with pool.acquire() as conn, conn.transaction():
            instrument_id = await _instrument_id(conn)
            audit_event_id = await _audit_event_id(conn)
            batch = _tick_batch(
                instrument_id=instrument_id,
                audit_event_id=audit_event_id,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
                accepted=-1,
            )
            await batch_repo.create_tick_batch(conn, batch)


@pytest.mark.perf
async def test_create_and_get_tick_batch_round_trip_count_bounded(pool, batch_repo):
    """수치 성능 단언(D2): create_tick_batch/get_tick_batch가 각각 정확히
    DB 왕복 1회만 내는지 증명한다."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        audit_event_id = await _audit_event_id(conn)
        batch = _tick_batch(
            instrument_id=instrument_id,
            audit_event_id=audit_event_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )

        create_queries: list[str] = []
        create_logger = _query_logger(create_queries)
        conn.add_query_logger(create_logger)
        try:
            await batch_repo.create_tick_batch(conn, batch)
        finally:
            conn.remove_query_logger(create_logger)

        get_queries: list[str] = []
        get_logger = _query_logger(get_queries)
        conn.add_query_logger(get_logger)
        try:
            fetched = await batch_repo.get_tick_batch(conn, batch.batch_id, None)
        finally:
            conn.remove_query_logger(get_logger)

    print(
        f"\nmarket_data tick_batch round trips: create={len(create_queries)} "
        f"get={len(get_queries)} (max create={_MAX_CREATE_ROUND_TRIPS}, "
        f"max get={_MAX_GET_ROUND_TRIPS})"
    )
    assert fetched is not None
    assert len(create_queries) <= _MAX_CREATE_ROUND_TRIPS, (
        f"create_tick_batch() DB 왕복 수({len(create_queries)})가 상한"
        f"({_MAX_CREATE_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
    assert len(get_queries) <= _MAX_GET_ROUND_TRIPS, (
        f"get_tick_batch() DB 왕복 수({len(get_queries)})가 상한"
        f"({_MAX_GET_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )


class _ChattyBatchRepository(PostgresBatchRepository):
    """게이트 적색 재현(D3): `get_tick_batch` 전에 불필요한 왕복을 하나 더
    내는 회귀의 최소 재현(`test_get_candles.py` `_ChattyCandleStore`와
    동일 기법)."""

    async def get_tick_batch(self, conn, batch_id, tenant_id):
        await conn.fetchval("SELECT 1")
        return await super().get_tick_batch(conn, batch_id, tenant_id)


@pytest.mark.perf
async def test_get_tick_batch_round_trip_gate_detects_extra_query(pool, batch_repo):
    """게이트 적색 재현(D3): 왕복을 하나 더 내는 저장소를 끼우면 위 상한(1)
    게이트가 실제로 넘는지 증명한다(I-10 — 게이트는 "있다"가 아니라
    "작동함이 증명됨"이어야 한다)."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        audit_event_id = await _audit_event_id(conn)
        batch = _tick_batch(
            instrument_id=instrument_id,
            audit_event_id=audit_event_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
        )
        await batch_repo.create_tick_batch(conn, batch)

    chatty = _ChattyBatchRepository(pool)
    async with pool.acquire() as conn:
        queries: list[str] = []
        logger = _query_logger(queries)
        conn.add_query_logger(logger)
        try:
            fetched = await chatty.get_tick_batch(conn, batch.batch_id, None)
        finally:
            conn.remove_query_logger(logger)

    assert fetched is not None
    assert len(queries) == _MAX_GET_ROUND_TRIPS + 1
    assert len(queries) > _MAX_GET_ROUND_TRIPS


async def test_concurrent_create_tick_batch_same_batch_id_exactly_one_succeeds(pool, batch_repo):
    """동시성 증명(D3): 같은 batch_id로 5-way 동시 create_tick_batch를
    보내면 정확히 1건만 성공하고 나머지 4건은 DuplicateBatchError로
    거부된다 — PK UNIQUE 제약이 경합 상황에서도 fail-closed로 작동함을
    증명한다(task-424/614와 동일 패턴)."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)

    batch_id = uuid.uuid4()

    async def _attempt() -> str:
        async with pool.acquire() as conn, conn.transaction():
            audit_event_id = await _audit_event_id(conn)
            batch = TickIngestBatchResult(
                batch_id=batch_id,
                tenant_id=None,
                source="test",
                venue=Venue.BITGET,
                instrument_id=instrument_id,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
                request_fingerprint=f"fp-{uuid.uuid4().hex}",
                verdict=QualityVerdict(
                    verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
                ),
                batch_hash=f"hash-{uuid.uuid4().hex}",
                audit_event_id=audit_event_id,
            )
            try:
                await batch_repo.create_tick_batch(conn, batch)
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
