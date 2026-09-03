"""PostgresBatchRepository 틱 배치 메서드 통합테스트 — 실 DB(TEST_DATABASE_URL).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-16a.
DoD(task-656): 왕복(create_tick_batch → get_tick_batch, issues 포함) +
negative: 같은 batch_id 재삽입 거부, tenant 불일치는 조회 시 존재 자체를
숨긴다(§8.3 LA-21 "404 동형").
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import (
    DuplicateBatchError,
    PostgresBatchRepository,
)
from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    QualityVerdict,
    Severity,
    TickIngestBatchResult,
    Venue,
    Verdict,
)
from tests.integration.conftest import create_test_user


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
    tenant_id: uuid.UUID | None = None,
    accepted: int = 1,
    quarantined: int = 0,
    rejected: int = 0,
    issues: list[QualityIssue] | None = None,
) -> TickIngestBatchResult:
    return TickIngestBatchResult(
        batch_id=uuid.uuid4(),
        tenant_id=tenant_id,
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
            issues=issues or [],
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=audit_event_id,
    )


async def test_create_tick_batch_then_get_roundtrips_verdict_and_issues(pool, batch_repo):
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        audit_event_id = await _audit_event_id(conn)
        issue = QualityIssue(
            type=QualityIssueType.TIME_MISALIGNED,
            severity=Severity.WARN,
            open_time=t0,
            detail={"trade_id": "42"},
        )
        batch = _tick_batch(
            instrument_id=instrument_id,
            audit_event_id=audit_event_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            accepted=3,
            quarantined=1,
            rejected=0,
            issues=[issue],
        )
        created = await batch_repo.create_tick_batch(conn, batch)
        fetched = await batch_repo.get_tick_batch(conn, created.batch_id, None)

    assert fetched is not None
    assert fetched.batch_id == batch.batch_id
    assert fetched.verdict.accepted == 3
    assert fetched.verdict.quarantined == 1
    assert fetched.verdict.rejected == 0
    assert len(fetched.verdict.issues) == 1
    assert fetched.verdict.issues[0].type == QualityIssueType.TIME_MISALIGNED
    assert fetched.verdict.issues[0].detail == {"trade_id": "42"}


async def test_create_tick_batch_duplicate_batch_id_raises(pool, batch_repo):
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

    # PK 위반은 트랜잭션을 abort 상태로 만들어 그 안의 이후 COMMIT을 깨뜨리므로
    # (test_candle_store.py의 같은 패턴), 실패할 create는 별도 트랜잭션으로.
    with pytest.raises(DuplicateBatchError):
        async with pool.acquire() as conn, conn.transaction():
            await batch_repo.create_tick_batch(conn, batch)


async def test_get_tick_batch_cross_tenant_lookup_returns_none(pool, batch_repo):
    """negative: 다른 tenant의 batch_id로 조회하면 존재 자체를 숨기고 None을
    반환해야 한다(§8.3 LA-21 "404 동형") — 없어서 None인지 남의 tenant
    것이라 None인지 호출부가 구분할 수 없어야 한다."""
    owner_tenant = await create_test_user(pool)
    other_tenant = await create_test_user(pool)
    t0 = datetime.now(timezone.utc).replace(microsecond=0)
    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        audit_event_id = await _audit_event_id(conn)
        batch = _tick_batch(
            instrument_id=instrument_id,
            audit_event_id=audit_event_id,
            range_start=t0,
            range_end=t0 + timedelta(minutes=1),
            tenant_id=owner_tenant,
        )
        await batch_repo.create_tick_batch(conn, batch)

    async with pool.acquire() as conn:
        as_owner = await batch_repo.get_tick_batch(conn, batch.batch_id, owner_tenant)
        as_other = await batch_repo.get_tick_batch(conn, batch.batch_id, other_tenant)
        as_missing = await batch_repo.get_tick_batch(conn, uuid.uuid4(), other_tenant)

    assert as_owner is not None
    assert as_other is None
    assert as_missing is None
