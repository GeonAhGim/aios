"""UX-6 `adapters/postgres_field_source.py` + full-stack `run_screen` integration
tests (real DB, TEST_DATABASE_URL).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §8("테넌트 격리:
교차 테넌트 404를 화면 레벨에서 재확인"), §9 UX-6 DoD.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_batch_repository import PostgresBatchRepository
from src.foundation.market_data.adapters.storage.hot_postgres import HotPostgresStorage
from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    IngestBatchResult,
    QualityVerdict,
    SeriesKey,
    Timeframe,
    Venue,
    Verdict,
)
from src.foundation.screener.adapters.postgres_field_source import PostgresScreenerFieldSource
from src.foundation.screener.adapters.postgres_repository import PostgresSavedScreenerRepository
from src.foundation.screener.application.run_screen import ScreenResultCache, run_saved_screen
from src.foundation.screener.contracts.v1 import IndicatorFilter, ScreenDefinition
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def field_source(pool: asyncpg.Pool) -> PostgresScreenerFieldSource:
    return PostgresScreenerFieldSource(pool)


@pytest.fixture
def batch_repo(pool: asyncpg.Pool) -> PostgresBatchRepository:
    return PostgresBatchRepository(pool)


@pytest.fixture
def hot_storage(pool: asyncpg.Pool) -> HotPostgresStorage:
    return HotPostgresStorage(pool)


async def _audit_event_id(conn: asyncpg.Connection) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO foundation_audit_event "
        "(sequence_no, aggregate_type, aggregate_id, action, outcome, trace_id, "
        " payload_hash, payload, event_hash) "
        "VALUES ($1, 'test.screener', gen_random_uuid(), 'test.screener.ingest', 'SUCCESS', "
        " gen_random_uuid(), 'deadbeef', '{}'::jsonb, 'deadbeef') RETURNING id",
        uuid.uuid4().int % (2**62),
    )


async def _instrument_id(
    conn: asyncpg.Connection, venue: Venue, *, prefix: str = "UX6"
) -> uuid.UUID:
    symbol = f"{prefix}-{uuid.uuid4().hex}"
    asset_class = "CRYPTO" if venue == Venue.BITGET else "KR_EQUITY"
    return await conn.fetchval(
        "INSERT INTO md_instrument "
        "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
        " status, listed_at) "
        "VALUES ($1, $2, $2, $3, 0.01, 1, 'LISTED', now()) RETURNING instrument_id",
        venue.value,
        symbol,
        asset_class,
    )


async def _write_daily_candle(
    pool: asyncpg.Pool,
    batch_repo: PostgresBatchRepository,
    hot_storage: HotPostgresStorage,
    *,
    instrument_id: uuid.UUID,
    venue: Venue,
    open_time: datetime,
    close: Decimal,
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        audit_event_id = await _audit_event_id(conn)
        batch = IngestBatchResult(
            batch_id=uuid.uuid4(),
            source="test",
            venue=venue,
            instrument_id=instrument_id,
            timeframe=Timeframe.D1,
            range_start=open_time,
            range_end=open_time + timedelta(days=1),
            request_fingerprint=f"fp-{uuid.uuid4().hex}",
            verdict=QualityVerdict(
                verdict=Verdict.ACCEPT, accepted=1, quarantined=0, rejected=0, issues=[]
            ),
            batch_hash=f"hash-{uuid.uuid4().hex}",
            audit_event_id=audit_event_id,
            stored_range=None,
        )
        await batch_repo.create(conn, batch)
        key = SeriesKey(venue=venue, instrument_id=instrument_id, timeframe=Timeframe.D1)
        candle = CandleRecord(
            key=key,
            open_time=open_time,
            close_time=open_time + timedelta(days=1),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1000"),
        )
        await hot_storage.write_batch(conn, batch.batch_id, [candle])


# ---- universe_page: LA-24 index reuse, venue-scoped ----


async def test_universe_page_returns_only_requested_venue(pool, field_source) -> None:
    async with pool.acquire() as conn:
        krx_id = await _instrument_id(conn, Venue.KIS_KRX)
        await _instrument_id(conn, Venue.BITGET)

    page = await field_source.universe_page(
        venues=frozenset({Venue.KIS_KRX}), after=None, limit=1000
    )

    ids = {ref.instrument_id for ref in page}
    assert krx_id in ids
    assert all(ref.venue == Venue.KIS_KRX for ref in page)


# ---- negative: empty venue set is fail-closed (no accidental "all venues") ----


async def test_universe_page_empty_venues_returns_empty(pool, field_source) -> None:
    assert await field_source.universe_page(venues=frozenset(), after=None, limit=10) == []


# ---- read_fields: batched latest-bar lookup, grouped per venue ----


async def test_read_fields_returns_latest_close_grouped_by_venue(
    pool, field_source, batch_repo, hot_storage
) -> None:
    t0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.acquire() as conn:
        krx_id = await _instrument_id(conn, Venue.KIS_KRX)
        bitget_id = await _instrument_id(conn, Venue.BITGET)
    await _write_daily_candle(
        pool,
        batch_repo,
        hot_storage,
        instrument_id=krx_id,
        venue=Venue.KIS_KRX,
        open_time=t0,
        close=Decimal("55000"),
    )
    await _write_daily_candle(
        pool,
        batch_repo,
        hot_storage,
        instrument_id=bitget_id,
        venue=Venue.BITGET,
        open_time=t0,
        close=Decimal("0.5"),
    )

    result = await field_source.read_fields(
        instrument_ids_by_venue={Venue.KIS_KRX: [krx_id], Venue.BITGET: [bitget_id]},
        field_names=frozenset({"close"}),
        as_of=t0 + timedelta(days=1),
    )

    assert result[krx_id] == {"close": Decimal("55000")}
    assert result[bitget_id] == {"close": Decimal("0.5")}


# ---- negative/fail-closed: an instrument with no candle is absent, not 0-filled ----


async def test_read_fields_excludes_instrument_with_no_candle(pool, field_source) -> None:
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, Venue.KIS_KRX)

    result = await field_source.read_fields(
        instrument_ids_by_venue={Venue.KIS_KRX: [instrument_id]},
        field_names=frozenset({"close"}),
        as_of=datetime.now(timezone.utc),
    )

    assert instrument_id not in result


# ---- full-stack: cross-tenant 404 re-checked at the screen level (§8) ----


async def test_run_saved_screen_cross_tenant_is_404_end_to_end(
    pool, field_source, batch_repo, hot_storage
) -> None:
    t0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.acquire() as conn:
        instrument_id = await _instrument_id(conn, Venue.KIS_KRX)
    await _write_daily_candle(
        pool,
        batch_repo,
        hot_storage,
        instrument_id=instrument_id,
        venue=Venue.KIS_KRX,
        open_time=t0,
        close=Decimal("60000"),
    )

    repo = PostgresSavedScreenerRepository(pool)
    owner = await create_test_tenant(pool)
    stranger = await create_test_tenant(pool)
    saved = await repo.save(
        tenant_id=owner,
        name="ux6-e2e",
        definition=ScreenDefinition(
            universe="KIS_KRX", filters=(IndicatorFilter(condition="close > 100"),)
        ),
    )

    cross_tenant_result = await run_saved_screen(
        stranger, saved.id, repo=repo, field_source=field_source, cache=ScreenResultCache()
    )
    owner_result = await run_saved_screen(
        owner, saved.id, repo=repo, field_source=field_source, cache=ScreenResultCache()
    )

    assert cross_tenant_result is None
    assert owner_result is not None
    # The KIS_KRX venue is shared reference data across every test in this DB
    # (md_instrument is not tenant-scoped), so other tests' rows may also match
    # "close > 100" — assert this run's own row is present, not an exact total.
    matched = {r.instrument_id: r for r in owner_result.rows}
    assert instrument_id in matched
    assert matched[instrument_id].values["close"] == Decimal("60000")
