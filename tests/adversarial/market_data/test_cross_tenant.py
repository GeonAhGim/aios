"""LA-21 적대적 — tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LA-21
("tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형)").

**실결함(task-655 발견, decision: 이 리프에서 src 수정 금지, skip으로
남김)**: `BatchRepository.get(conn, batch_id)`
(`ports/batch_repository.py`)와 그 구현체
`PostgresBatchRepository.get()`(`adapters/postgres_batch_repository.py`)
둘 다 `tenant_id` 파라미터 자체가 없다 — `md_ingest_batch.tenant_id`
컬럼은 존재하지만(`4a1d0c0de008_md_candles.py`) 조회 시 대조하는 곳이
어디에도 없다. 즉 `batch_id`(UUID)만 알아내면 그 배치를 만든 tenant가
누구든 상관없이 `md_ingest_batch` 행 전체가 그대로 반환된다 — 스펙이
요구하는 "존재 자체 비노출"을 지금 코드는 지키지 못한다.

이 테스트는 그 결함을 있는 그대로 재현해 항상 실패하도록 작성했다
(`pytest.raises`로 감쌀 대상 예외가 애초에 없다 — `get()`이 tenant를
구분할 방법이 없으므로 "B로 조회"를 표현할 수조차 없다). `xfail`로
조용히 넘기지 않고 `skip`으로 남기는 이유는 이 결함이 시그니처 자체의
부재(구현 버그가 아니라 포트 계약의 공백)라 — 후속 리프
**LA-22(task-825)**가 `BatchRepository.get(conn, batch_id, tenant_id)`로
포트 시그니처를 넓히고 어댑터에 `WHERE tenant_id = $2` 필터를 추가한
뒤에야 이 테스트를 의미 있게 다시 쓸 수 있다. LA-22 완료 후 아래 skip을
지우고, `get()` 호출부에 `tenant_id=attacker_id`를 실제로 전달하도록
고쳐 재실행할 것.
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


@pytest.mark.skip(
    reason=(
        "실결함(task-655 발견, decision, needs LA-22/task-825): "
        "BatchRepository.get(conn, batch_id)에 tenant_id 파라미터 자체가 "
        "없어(ports/batch_repository.py, adapters/postgres_batch_repository.py) "
        "attacker가 batch_id만 알면 tenant A의 md_ingest_batch 행이 그대로 "
        "반환된다 — '존재 자체 비노출(404 동형)' 요구를 지금 코드로는 "
        "표현·검증할 방법이 없다. src(ports/adapters)는 이 리프 범위 밖이라 "
        "수정하지 않았다. LA-22가 get()에 tenant_id 필터를 추가한 뒤 이 "
        "테스트를 다시 활성화할 것(아래 pytest.fail을 실제 assert로 교체)."
    )
)
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
        # get()에는 tenant_id를 넘길 방법이 없다 — 즉 "attacker_id로 조회"를
        # 표현할 수 없고, 아래 호출은 owner가 하든 attacker가 하든 항상 같다.
        leaked = await batch_repo.get(conn, batch.batch_id)

    assert leaked is not None and leaked.tenant_id == owner_id, (
        "결함 전제(배치가 존재하고 tenant A 소유)가 재현되지 않았습니다"
    )
    pytest.fail(
        "BatchRepository.get()이 tenant를 구분하지 않아 attacker_id로도 "
        "tenant A의 배치가 그대로 조회됩니다(§8.3 '404 동형' 위반) — "
        "LA-22(task-825)가 tenant_id 필터를 추가할 때까지 이 테스트는 "
        "의도적으로 항상 실패합니다."
    )
