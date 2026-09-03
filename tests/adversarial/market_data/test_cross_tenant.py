"""LA-21 적대적 — tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LA-21
("tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형)").

task-655가 실증한 실결함(`BatchRepository.get(conn, batch_id)`에
`tenant_id` 인자 자체가 없어 아무 tenant나 배치를 조회할 수 있었던 문제)은
**LA-22(task-825)**가 고쳤다 — `get()`에 `tenant_id` 파라미터를 추가하고
`WHERE tenant_id IS NOT DISTINCT FROM $2`로 필터한다
(`ports/batch_repository.py`, `adapters/postgres_batch_repository.py`).
이 테스트는 attacker가 자신의 `tenant_id`로 owner의 `batch_id`를 조회할 때
"행이 없음"과 동형으로 `None`이 반환됨을(§8.3 "404 동형") 검증한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
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
from tests.integration.conftest import create_test_user


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


async def _seed_owner_batch(
    conn: asyncpg.Connection, batch_repo, candle_store, *, tenant_id, instrument_id
) -> IngestBatchResult:
    audit_event_id = await _audit_event_id(conn)
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    candle = CandleRecord(
        key=key, open_time=t0, close_time=t0 + timedelta(minutes=1),
        open=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("105"),
        volume=Decimal("10"),
    )
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(), tenant_id=tenant_id, source="test", venue=Venue.BITGET,
        instrument_id=instrument_id, timeframe=Timeframe.M1, range_start=t0,
        range_end=t0 + timedelta(minutes=1), request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0,
                                issues=[]),
        batch_hash=f"hash-{uuid.uuid4().hex}", audit_event_id=audit_event_id, stored_range=None,
    )
    await batch_repo.create(conn, batch)
    await candle_store.upsert_batch(conn, batch.batch_id, [candle])
    return batch


async def test_cross_tenant_batch_get_does_not_leak_existence(pool, batch_repo, candle_store):
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    assert attacker_id != owner_id

    async with pool.acquire() as conn, conn.transaction():
        instrument_id = await _instrument_id(conn)
        batch = await _seed_owner_batch(
            conn, batch_repo, candle_store, tenant_id=owner_id, instrument_id=instrument_id
        )

    async with pool.acquire() as conn:
        owner_view = await batch_repo.get(conn, batch.batch_id, owner_id)
        attacker_view = await batch_repo.get(conn, batch.batch_id, attacker_id)
        missing_view = await batch_repo.get(conn, uuid.uuid4(), attacker_id)

    assert owner_view is not None and owner_view.tenant_id == owner_id, (
        "결함 전제(배치가 존재하고 tenant A 소유)가 재현되지 않았습니다"
    )
    assert attacker_view is None, (
        "BatchRepository.get()이 tenant를 구분하지 않아 attacker_id로도 "
        "tenant A의 배치가 조회됩니다(§8.3 '404 동형' 위반)"
    )
    assert missing_view is None, (
        "다른 tenant 소유 배치와 존재하지 않는 배치가 같은 None으로 "
        "동형이어야 한다(§8.3 '존재 자체 비노출')"
    )
