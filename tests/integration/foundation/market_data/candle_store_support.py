"""test_candle_store 공용 헬퍼 — CTO 2026-09-23 분할.

loc_over_500 래칫: ddb6ebea가 test_candle_store.py를 504줄로 넘겨 CHECK 제약 테스트와
헬퍼를 분리했다(픽스처 candle_store/batch_repo는 conftest.py).

task-10255 DEEPEN: 이 헬퍼 모듈 자체는 어느 `test_*.py`에서도 직접 negative/실패주입
대상이 아니었다(소비자인 test_candle_store_checks.py 등은 헬퍼가 만든 *유효한* 데이터로
CHECK 위반을 검증할 뿐, 헬퍼 자신이 의존하는 FK/UNIQUE 제약이나 커넥션 실패 전파는
다루지 않는다). 아래는 그 공백만 채운다 — pytest는 명시적으로 지정된 파일 경로는
`python_files` 패턴과 무관하게 수집한다(실측 확인).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    PostgresBatchRepository,
)
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)


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


# --- negative / failure-injection (task-10255 DEEPEN) ----------------------
#
# 소비자 테스트들(test_candle_store_checks.py 등)은 위 헬퍼가 만든 *유효한*
# instrument_id/audit_event_id로 CHECK 위반만 검증한다. 아래는 헬퍼 자신이
# 암묵적으로 의존하는 FK/UNIQUE 제약과, 의존 커넥션이 실패했을 때 예외를
# 삼키지 않고 그대로 전파하는지(fail-closed)를 직접 겨냥한다.


class _ExplodingConnection:
    """failure-injection: 실제 커넥션을 감싸되 `fetchval`만 주입된 예외로 교체한다."""

    def __init__(self, real: asyncpg.Connection, exc: Exception) -> None:
        self._real = real
        self._exc = exc

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        raise self._exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


async def test_instrument_id_propagates_connection_failure(
    pool: asyncpg.Pool[asyncpg.Connection],
) -> None:
    """failure-injection: `_instrument_id`의 `conn.fetchval`이 터지면 예외를
    삼키지 않고 그대로 위로 전파해야 한다(fail-closed — 조용히 None을 반환하면
    호출자가 instrument_id=None으로 다음 단계를 진행하는 더 위험한 상태가 된다)."""
    injected = asyncpg.PostgresConnectionError("boom (injected by test)")
    async with pool.acquire() as conn, conn.transaction():
        exploding = cast(asyncpg.Connection, _ExplodingConnection(conn, injected))
        with pytest.raises(asyncpg.PostgresConnectionError, match="boom"):
            await _instrument_id(exploding)


async def test_create_batch_rejects_unknown_instrument_id(
    pool: asyncpg.Pool[asyncpg.Connection], batch_repo: PostgresBatchRepository
) -> None:
    """negative: `_create_batch`에 `md_instrument`에 없는 instrument_id를
    넘기면 `md_ingest_batch.instrument_id` FK 위반으로 거부돼야 한다 — 헬퍼가
    DB 제약을 우회하지 않는다는 증명."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await _create_batch(
                conn,
                batch_repo,
                instrument_id=uuid.uuid4(),  # md_instrument에 존재하지 않음
                range_start=t0,
                range_end=t0 + timedelta(minutes=1),
            )


async def test_create_batch_rejects_unknown_audit_event_id(
    pool: asyncpg.Pool[asyncpg.Connection], batch_repo: PostgresBatchRepository
) -> None:
    """negative: `audit_event_id`가 `foundation_audit_event`에 실재하지 않으면
    (`_audit_event_id` 헬퍼를 우회해 직접 조작한 값) `md_ingest_batch.audit_event_id`
    FK 위반으로 거부돼야 한다 — None 거부(기존 테스트)와는 다른 제약 경로."""
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
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
            audit_event_id=uuid.uuid4(),  # foundation_audit_event에 존재하지 않음
            stored_range=None,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await batch_repo.create(conn, batch)


async def test_instrument_id_rejects_duplicate_symbol_same_listed_at(
    pool: asyncpg.Pool[asyncpg.Connection],
) -> None:
    """negative: `_instrument_id`가 만드는 행은 `UNIQUE (venue, canonical_symbol,
    listed_at)` 제약에 의존한다 — 같은 (venue, canonical_symbol, listed_at)로 두 번
    삽입하면 두 번째가 UNIQUE 위반으로 거부돼야 한다(헬퍼의 uuid 기반 심볼 무작위성이
    이 제약을 우회하지 않는다는 증명)."""
    symbol = f"TEST-DUP-{uuid.uuid4().hex}"
    listed_at = datetime.now(timezone.utc).replace(microsecond=0)

    async def _insert(conn: asyncpg.Connection) -> uuid.UUID:
        return cast(
            uuid.UUID,
            await conn.fetchval(
                "INSERT INTO md_instrument "
                "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
                " status, listed_at) "
                "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', $2) "
                "RETURNING instrument_id",
                symbol,
                listed_at,
            ),
        )

    async with pool.acquire() as conn, conn.transaction():
        await _insert(conn)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert(conn)
