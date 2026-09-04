"""LA-16 ingest_ticks 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-16.
DoD(task-842): 정상 배치 저장+배치행+감사 이벤트 1건; trade_id 역행 배치
전량 REJECT(부분 저장 금지); 재수집 멱등(md_tick 중복 없이 배치행만 새로
남음); 감사 실패 주입 → md_tick·md_ingest_batch_tick 전부 롤백.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import asyncpg
import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.application.ingest_ticks import IngestTicksCommand, ingest_ticks
from src.foundation.market_data.contracts.v1 import TickRecord, Venue, Verdict


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


def _tick(instrument_id: uuid.UUID, trade_id: str, t: datetime, price: str = "100") -> TickRecord:
    return TickRecord(
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        trade_id=trade_id,
        price=Decimal(price),
        quantity=Decimal("1"),
        side="buy",
        traded_at=t,
    )


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def deps(pool):
    return SimpleNamespace(
        pool=pool,
        audit=PostgresAuditEventRepository(pool),
        batches=PostgresBatchRepository(pool),
    )


async def _run(deps, ticks, *, audit=None) -> object:
    cmd = IngestTicksCommand(
        tenant_id=None, source="test", ticks=ticks, trace_id=uuid.uuid4()
    )
    return await ingest_ticks(
        cmd, batches=deps.batches, audit=audit or deps.audit, pool=deps.pool
    )


async def test_ingest_accepts_and_stores_ticks_with_one_audit_event(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [
        _tick(instrument_id, "1", t0),
        _tick(instrument_id, "2", t0 + timedelta(seconds=1)),
    ]

    result = await _run(deps, ticks)

    assert result.verdict.verdict == Verdict.ACCEPT
    assert result.verdict.accepted == 2
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        events = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
        batch_rows = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE id = $1", result.batch_id
        )
    assert stored == 2
    assert events == 1
    assert batch_rows == 1


async def test_ingest_rejects_whole_batch_on_trade_id_regression(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    first = [
        _tick(instrument_id, "1", t0),
        _tick(instrument_id, "3", t0 + timedelta(seconds=2)),
    ]
    await _run(deps, first)

    regressive = [
        _tick(instrument_id, "2", t0 + timedelta(seconds=3)),
        _tick(instrument_id, "4", t0 + timedelta(seconds=4)),
    ]
    result = await _run(deps, regressive)

    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 2
    assert result.verdict.accepted == 0
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id IN ('2', '4')",
            instrument_id,
        )
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
    assert stored == 0, "역행 배치는 부분 저장 없이 전량 REJECT되어야 한다"
    assert outcome == "DENIED"


async def test_ingest_reingest_is_idempotent_and_creates_new_batch_row(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0), _tick(instrument_id, "2", t0 + timedelta(seconds=1))]

    first = await _run(deps, ticks)
    second = await _run(deps, ticks)

    assert first.batch_id != second.batch_id
    assert second.verdict.verdict == Verdict.ACCEPT, "같은 배치 재수집은 역행이 아니다"
    async with pool.acquire() as conn:
        tick_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE instrument_id = $1", instrument_id
        )
    assert tick_count == 2, "재수집은 멱등해야 한다(같은 틱이 두 번 저장되지 않음)"
    assert batch_count == 2, "배치 기록 자체는 호출마다 새로 남는다(INSERT only)"


async def test_ingest_rejects_regression_hidden_by_same_timestamp_tie(pool, deps):
    """동시 체결(같은 traded_at)로 저장된 두 trade_id 중 baseline이 더 작은
    쪽으로 뽑히더라도(같은 트랜잭션은 created_at도 동일해 단순 LIMIT 1
    tie-break로는 구분 불가) 그 사이 값 재수집은 여전히 역행으로 잡혀야
    한다(QA 발견, task-1004)."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)

    first = [_tick(instrument_id, "5", t0), _tick(instrument_id, "9", t0)]
    r1 = await _run(deps, first)
    assert r1.verdict.verdict == Verdict.ACCEPT

    second = [_tick(instrument_id, "7", t0)]
    r2 = await _run(deps, second)

    assert r2.verdict.verdict == Verdict.REJECT
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '7'",
            instrument_id,
        )
    assert stored == 0


async def test_ingest_rejects_regression_when_same_trade_id_reused_at_earlier_time(pool, deps):
    """같은 trade_id를 다른(더 이른) traded_at으로 재수집하면 `md_tick`
    UNIQUE(venue, instrument_id, trade_id, traded_at)와 다른 복합키라
    "재수집"이 아니라 신규 행이다 — trade_id만으로 "이미 안다"고 판정하면
    이 배치가 역행검사를 건너뛰고 통과해버린다(리뷰 REJECT, task-1302).
    올바른 구현은 traded_at 역행으로 배치 전체를 REJECT하고 새 행을
    남기지 않아야 한다."""
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)

    first = [_tick(instrument_id, "1", t0), _tick(instrument_id, "5", t0 + timedelta(seconds=5))]
    r1 = await _run(deps, first)
    assert r1.verdict.verdict == Verdict.ACCEPT

    regressive = [_tick(instrument_id, "5", t0 + timedelta(seconds=2))]
    result = await _run(deps, regressive)

    assert result.verdict.verdict == Verdict.REJECT
    assert result.verdict.rejected == 1
    assert result.verdict.accepted == 0
    async with pool.acquire() as conn:
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1 AND trade_id = '5'",
            instrument_id,
        )
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1", result.batch_id
        )
    assert row_count == 1, "역행 배치는 새 행을 남기지 않아야 한다(기존 1행만 존재)"
    assert outcome == "DENIED"


async def test_ingest_rolls_back_md_tick_on_audit_failure(pool, deps):
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    ticks = [_tick(instrument_id, "1", t0)]

    with pytest.raises(RuntimeError):
        await _run(deps, ticks, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        tick_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
        batch_count = await conn.fetchval(
            "SELECT COUNT(*) FROM md_ingest_batch_tick WHERE instrument_id = $1", instrument_id
        )
    assert tick_count == 0
    assert batch_count == 0
