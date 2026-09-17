"""LA-21 적대적 — 저장 캔들 변조: superuser UPDATE 후 replay.series_hash
변화 + 배치 해시 재검증 실패 감지.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LA-21
("저장 캔들 superuser UPDATE 후 `replay.series_hash` 변화 + 배치 해시
재검증 실패 감지").

`md_candle`은 WORM(L0-3, `md_candle_worm_guard_trg`)이라 일반 경로로는
UPDATE가 거부된다 — `test_verify_integrity.py`(LC-10)와 같은 방식으로
트리거를 일시 DISABLE해 "superuser가 트리거까지 우회해 직접 행을
바꿨다"는 공격을 재현하고, try/finally로 손상 주입~복원을 감싼다. 변조
값은 CHECK 제약(`high >= close`, `low <= close`)을 우회하지 않는다 —
트리거를 꺼도 CHECK는 그대로 강제되므로(WORM은 append-only만 막는다),
원본 범위(low=90, high=110) 안에서 값만 바꿔 "체크는 통과하지만 내용이
달라진" 변조를 재현한다.

두 가지 탐지 경로를 각각 확인한다:
1. `replay()`의 `series_hash`(`domain/lineage.batch_hash`, LA-17)가 변조
   전후로 달라진다 — 리플레이 소비자가 조용히 다른 데이터를 받지 않는다.
2. 저장 직후(변조 전) 실제 저장된 캔들로 계산한 배치 해시와, 변조 후
   같은 방식으로 다시 계산한 해시가 더는 일치하지 않는다 — 배치 무결성
   재검증이 변조를 잡아낸다. 두 값 모두 `candle_store.query()`로 DB에서
   다시 읽어온 레코드로 계산한다(수집 직전 in-memory `CandleRecord`와
   비교하지 않는다 — `NUMERIC(30,10)` 왕복은 `Decimal("100")`을
   `Decimal("100.0000000000")`로 돌려줘 자릿수 표현이 달라지고,
   `batch_hash`는 canonical JSON 문자열을 그대로 해시하므로 값이 같아도
   자릿수가 다르면 다른 해시가 나온다 — 이는 변조가 아니라 왕복
   직렬화 차이이므로 재검증은 항상 "저장된 형태"끼리 비교해야 한다).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
from src.foundation.market_data.application.replay_candles import replay
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    ReplayRequest,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.market_data.domain.lineage import batch_hash as compute_batch_hash

_WORM_TRIGGER = "md_candle_worm_guard_trg"
_ORIGINAL_CLOSE = Decimal("105")
_TAMPERED_CLOSE = Decimal("100.5")


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
    key: SeriesKey, open_time: datetime, o: float, h: float, low: float, c: Decimal, v: float
) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=c,
        volume=Decimal(str(v)),
    )


async def _seed_batch(
    conn: asyncpg.Connection, batch_repo, candle_store, *, instrument_id, key, opens
) -> IngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    candles = [_candle(key, ot, 100, 110, 90, _ORIGINAL_CLOSE, 10) for ot in opens]
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=opens[0],
        range_end=opens[-1] + timedelta(minutes=1),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=len(opens), quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
        stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(conn, batch.batch_id, candles)
    return batch


async def _set_close(
    pool, *, venue: str, instrument_id, timeframe: str, open_time: datetime, new_close: Decimal
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE md_candle DISABLE TRIGGER {_WORM_TRIGGER}")
        try:
            await conn.execute(
                "UPDATE md_candle SET close = $1 "
                "WHERE venue = $2 AND instrument_id = $3 AND timeframe = $4 AND open_time = $5",
                new_close,
                venue,
                instrument_id,
                timeframe,
                open_time,
            )
        finally:
            await conn.execute(f"ALTER TABLE md_candle ENABLE TRIGGER {_WORM_TRIGGER}")


async def test_tamper_changes_replay_series_hash(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        opens = [t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)]
        await _seed_batch(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=opens
        )
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=3), as_of=as_of)
    before = await replay(
        request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
    )

    try:
        await _set_close(
            pool,
            venue=Venue.BITGET.value,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1.value,
            open_time=t0,
            new_close=_TAMPERED_CLOSE,
        )

        after = await replay(
            request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool
        )
        assert after.series_hash != before.series_hash, (
            "superuser 변조 후에도 series_hash가 그대로입니다 — "
            "리플레이가 변조를 감지하지 못했습니다"
        )
    finally:
        await _set_close(
            pool,
            venue=Venue.BITGET.value,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1.value,
            open_time=t0,
            new_close=_ORIGINAL_CLOSE,
        )


async def test_worm_trigger_blocks_normal_update_without_trigger_disable(
    pool, candle_store, batch_repo
):
    """negative — 위 두 테스트는 트리거를 일시 DISABLE한 "superuser가 트리거까지
    우회"하는 최악의 경우를 재현한다. 이 테스트는 그 우회 없이 일반 경로로
    같은 UPDATE를 시도하면 WORM 트리거(`md_candle_worm_guard_trg`)가 실제로
    막는지 증명한다(`tests/adversarial/risk/test_worm_tables.py`의
    `test_worm_trigger_blocks_table_owner_update`와 동일 패턴). `pool`은
    `SET ROLE` 없이 테이블 소유자(마이그레이션 실행 계정)로 접속하므로,
    여기서 막힌다면 REVOKE(비소유자 방어)가 아니라 트리거 자체가 소유자에게도
    예외 없이 발동한다는 뜻이다(I-10 '우회불가 배선증명')."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _seed_batch(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=[t0]
        )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE md_candle SET close = $1 "
                "WHERE venue = $2 AND instrument_id = $3 AND timeframe = $4 AND open_time = $5",
                _TAMPERED_CLOSE,
                Venue.BITGET.value,
                instrument_id,
                Timeframe.M1.value,
                t0,
            )


async def test_tamper_breaks_batch_hash_reverification(
    pool, candle_store, batch_repo, reference_repo, calendar_repo
):
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        opens = [t0, t0 + timedelta(minutes=1)]
        await _seed_batch(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=opens
        )

    async with pool.acquire() as conn:
        stored = await candle_store.query(conn, key, t0, t0 + timedelta(minutes=2), as_of=None)
    hash_before = compute_batch_hash(stored)

    try:
        await _set_close(
            pool,
            venue=Venue.BITGET.value,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1.value,
            open_time=t0,
            new_close=_TAMPERED_CLOSE,
        )

        async with pool.acquire() as conn:
            tampered = await candle_store.query(
                conn, key, t0, t0 + timedelta(minutes=2), as_of=None
            )
        hash_after = compute_batch_hash(tampered)
        assert hash_after != hash_before, (
            "변조 후에도 재계산 배치 해시가 변조 전과 일치합니다 — "
            "재검증이 변조를 감지하지 못했습니다"
        )
    finally:
        await _set_close(
            pool,
            venue=Venue.BITGET.value,
            instrument_id=instrument_id,
            timeframe=Timeframe.M1.value,
            open_time=t0,
            new_close=_ORIGINAL_CLOSE,
        )


# ── 추가 negative tests (불변식 위반 입력을 명시적으로 거부) ──


async def test_worm_trigger_blocks_normal_delete_without_trigger_disable(
    pool, candle_store, batch_repo
):
    """negative — WORM 트리거는 `BEFORE UPDATE OR DELETE`로 걸려 있어
    (`src/core/db/append_only.py`) UPDATE뿐 아니라 DELETE도 막는다. 위
    `test_worm_trigger_blocks_normal_update_without_trigger_disable`이 변조
    경로(UPDATE)를 증명했으니, 여기서는 은폐 경로(DELETE)도 트리거 우회
    없이는 막힌다는 것을 증명한다 — 공격자가 흔적을 지우려 행을 통째로
    삭제해도 소유자 예외 없이 거부된다(I-10)."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _seed_batch(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=[t0]
        )

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM md_candle "
                "WHERE venue = $1 AND instrument_id = $2 AND timeframe = $3 AND open_time = $4",
                Venue.BITGET.value,
                instrument_id,
                Timeframe.M1.value,
                t0,
            )


async def test_upsert_batch_rejects_check_violating_candle(pool, candle_store, batch_repo):
    """negative — 앱 계층 품질 게이트(quality gate)를 아예 우회해 CHECK
    위반 캔들(`high < close`)을 `PostgresCandleStore.upsert_batch`에 직접
    넣어도(코드 검증이 뚫린 최악의 경우) DB CHECK 제약
    (`ck_md_candle_high_ge_close`)이 최종 방어선으로 거부한다 — 어댑터는
    이 위반을 삼키지 않고 `asyncpg.CheckViolationError`를 그대로
    전파한다(postgres_candle_store.py 모듈 docstring과 동일 계약)."""
    with pytest.raises(asyncpg.CheckViolationError, match="ck_md_candle_high_ge_close"):
        async with pool.acquire() as conn, conn.transaction():
            instrument_id = await _instrument_id(conn)
            key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
            t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            audit_event_id = await _audit_event_id(conn)
            batch = IngestBatchResult(
                batch_id=uuid.uuid4(),
                source="test",
                venue=Venue.BITGET,
                instrument_id=instrument_id,
                timeframe=Timeframe.M1,
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
                request_fingerprint=f"fp-{uuid.uuid4().hex}",
                verdict=QualityVerdict(
                    verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
                ),
                batch_hash=f"hash-{uuid.uuid4().hex}",
                audit_event_id=audit_event_id,
                stored_range=None,
            )
            await batch_repo.create(conn, batch)

            bad_candle = _candle(key, t0, 100, 100, 90, Decimal("105"), 10)
            await candle_store.upsert_batch(conn, batch.batch_id, [bad_candle])


# ── 추가 실패주입 (failure injection) ──


async def test_replay_fails_closed_when_batch_hash_dependency_raises(
    pool, candle_store, batch_repo, reference_repo, calendar_repo, monkeypatch
):
    """실패주입 — `series_hash` 계산에 쓰이는 `domain.lineage.batch_hash`가
    (배포 회귀 등으로) 예외를 던지면 `replay()`는 그 예외를 조용히 삼키고
    변조 탐지를 무력화하는 대체 해시로 넘어가는 대신 그대로 전파해야
    한다 — fail-closed가 기본 자세다(CLAUDE.md §3). 이 경로가 조용히
    성공하면 위 `test_tamper_changes_replay_series_hash`가 의존하는
    series_hash 비교 자체가 신뢰할 수 없게 된다."""
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
        t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _seed_batch(
            conn, batch_repo, candle_store, instrument_id=instrument_id, key=key, opens=[t0]
        )
        as_of = await conn.fetchval("SELECT now()")

    request = ReplayRequest(key=key, start=t0, end=t0 + timedelta(minutes=1), as_of=as_of)

    def _raise_dependency_failure(_candles):
        raise RuntimeError("batch_hash dependency failure (injected)")

    monkeypatch.setattr(
        "src.foundation.market_data.application.replay_candles.batch_hash",
        _raise_dependency_failure,
    )

    with pytest.raises(RuntimeError, match="batch_hash dependency failure"):
        await replay(request, store=candle_store, refs=reference_repo, cal=calendar_repo, pool=pool)
