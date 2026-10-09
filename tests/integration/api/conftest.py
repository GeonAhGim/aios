"""공유 fixture/헬퍼 — market_data 라우터 테스트 분할(test_market_data_router*.py)
전용. LA-24 HTTP read API, DC-28 source_contract 엔타이틀먼트 테스트가 함께 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.market_data import get_source_contract_repository
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
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/market-data"


def _source_contract(scope: RedistributionScope, *, source_id: str = "BITGET") -> SourceContract:
    now = datetime.now(timezone.utc)
    return SourceContract(
        source_id=source_id,
        tier=SourceContractTier.ENTERPRISE,
        credential_ref="test:none",
        redistribution_scope=scope,
        rate_limit=1000,
        quota=1_000_000,
        valid_from=now - timedelta(days=365),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"CRYPTO"}),
            resolutions=frozenset({"1m"}),
        ),
    )


class _FakeSourceContractRepository:
    """`SourceContractRepository`(포트) 페이크 — 실DB `source_contract` 행에
    의존하지 않고 테스트마다 원하는 스코프를 즉석에서 준다."""

    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return self._contract


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_source_contract_repository] = lambda: (
            _FakeSourceContractRepository(_source_contract(RedistributionScope.DISPLAY))
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_source_contract_repository, None)


async def _register(client: AsyncClient) -> tuple[dict, uuid.UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    token = response.json()["data"]["access_token"]
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return {"Authorization": f"Bearer {token}"}, uuid.UUID(user_id)


async def _seed_instrument(conn: asyncpg.Connection, listed_at: datetime) -> tuple[uuid.UUID, str]:
    symbol = f"TST{uuid.uuid4().hex[:10].upper()}"
    instrument_id = await conn.fetchval(
        "INSERT INTO md_instrument (venue, canonical_symbol, venue_symbol, asset_class, "
        " tick_size, lot_size, status, listed_at) "
        "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', $2) RETURNING instrument_id",
        symbol,
        listed_at,
    )
    await conn.execute(
        "INSERT INTO md_symbol_alias (instrument_id, venue, alias_symbol, valid_from) "
        "VALUES ($1, 'BITGET', $2, $3)",
        instrument_id,
        symbol,
        listed_at,
    )
    return instrument_id, symbol


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.market_data', gen_random_uuid(), 'test.md.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


def _candle(key: SeriesKey, open_time: datetime, price: int) -> CandleRecord:
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal(price),
        high=Decimal(price + 10),
        low=Decimal(price - 10),
        close=Decimal(price + 5),
        volume=Decimal(10),
    )


async def _seed_candles(
    conn: asyncpg.Connection, pool: asyncpg.Pool, instrument_id: uuid.UUID, t0: datetime, n: int
) -> SeriesKey:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=instrument_id, timeframe=Timeframe.M1)
    batch = IngestBatchResult(
        batch_id=uuid.uuid4(),
        source="test",
        venue=Venue.BITGET,
        instrument_id=instrument_id,
        timeframe=Timeframe.M1,
        range_start=t0,
        range_end=t0 + timedelta(minutes=n),
        request_fingerprint=f"fp-{uuid.uuid4().hex}",
        verdict=QualityVerdict(
            verdict=Verdict.ACCEPT, accepted=n, quarantined=0, rejected=0, issues=[]
        ),
        batch_hash=f"hash-{uuid.uuid4().hex}",
        audit_event_id=await _audit_event_id(conn),
        stored_range=None,
    )
    await PostgresBatchRepository(pool).create(conn, batch)
    candles = [_candle(key, t0 + timedelta(minutes=i), 100 + i) for i in range(n)]
    await PostgresCandleStore(pool).upsert_batch(conn, batch.batch_id, candles)
    return key


async def _grant_venue(conn: asyncpg.Connection, tenant_id: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $1, 'BITGET', '1m', 'DELAYED')",
        tenant_id,
    )


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    headers_b, _tenant_b = await _register(client)
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=30)
    pool = app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        instrument_id, symbol = await _seed_instrument(conn, t0 - timedelta(days=1))
        other_id, _ = await _seed_instrument(conn, t0 - timedelta(days=1))
        await _seed_candles(conn, pool, instrument_id, t0, 3)
        await _grant_venue(conn, tenant_a)
    return {
        "a": headers_a,
        "b": headers_b,
        "instrument_id": instrument_id,
        "other_id": other_id,
        "symbol": symbol,
        "t0": t0,
    }


def _span(t0: datetime, start_min: int, end_min: int) -> dict:
    return {
        "start": (t0 + timedelta(minutes=start_min)).isoformat(),
        "end": (t0 + timedelta(minutes=end_min)).isoformat(),
    }
