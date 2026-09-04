"""PostgresInstrumentRepository(DC-8) 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.2 DC-8. `ports/instrument_repository.py`(DC-5)를 그대로 구현한
어댑터가 DC-4 DB 제약(instrument_id 불변 트리거·venue_listings EXCLUDE)
위반을 도메인 예외로 정확히 변환하는지, 그리고 `get_listing`의 `at` 시점
스코프가 유효기간 밖 조회를 정확히 `None`으로 구분하는지 단언한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    DuplicateInstrumentIdError,
    InstrumentNotFoundError,
    PostgresInstrumentRepository,
    VenueListingOverlapError,
)
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _venue_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


def _instrument(**overrides) -> Instrument:
    fields = {
        "instrument_id": _fake_ulid(),
        "asset_class": AssetClass.CRYPTO,
        "base": "BTC",
        "quote": "USDT",
        "isin": None,
        "figi": None,
        "tick_size": Decimal("0.01"),
        "lot_size": Decimal("0.0001"),
        "calendar_id": "24x7",
        "lifecycle_state": InstrumentLifecycle.ACTIVE,
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return Instrument(**fields)


@pytest.fixture
def repo(pool):
    return PostgresInstrumentRepository(pool)


async def test_create_then_get_round_trips(pool, repo):
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        created = await repo.create(conn, instrument)
    assert created.instrument_id == instrument.instrument_id

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get(conn, instrument.instrument_id)
    assert found is not None
    assert found.instrument_id == instrument.instrument_id
    assert found.tick_size == instrument.tick_size


async def test_get_unknown_instrument_returns_none(pool, repo):
    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get(conn, _fake_ulid())
    assert found is None


async def test_create_duplicate_instrument_id_raises(pool, repo):
    """negative: §4.1 instrument_id 불변 — 재삽입은 예외로 거부돼야 한다."""
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument)

    with pytest.raises(DuplicateInstrumentIdError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.create(conn, instrument)


async def test_update_lifecycle_state_changes_state(pool, repo):
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument)

    async with pool.acquire() as conn, conn.transaction():
        updated = await repo.update_lifecycle_state(
            conn, instrument.instrument_id, InstrumentLifecycle.HALTED
        )
    assert updated.lifecycle_state == InstrumentLifecycle.HALTED


async def test_update_lifecycle_state_unknown_instrument_raises(pool, repo):
    """negative: 존재하지 않는 instrument_id는 조용히 0행 UPDATE로 넘어가지
    않고 fail-closed로 예외를 던져야 한다."""
    with pytest.raises(InstrumentNotFoundError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.update_lifecycle_state(conn, _fake_ulid(), InstrumentLifecycle.HALTED)


async def test_add_listing_then_get_listing_at_valid_time(pool, repo):
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument)

    symbol = _venue_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=5)
    listing = VenueListing(
        instrument_id=instrument.instrument_id,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=listed_at,
        delisted_at=None,
        is_primary=True,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.add_listing(conn, listing)

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_listing(
            conn, Venue.BITGET, symbol, datetime.now(timezone.utc)
        )
    assert found is not None
    assert found.instrument_id == instrument.instrument_id


async def test_get_listing_before_listed_at_returns_none(pool, repo):
    """negative/fail-closed: `at`이 `listed_at` 이전이면 아직 유효하지 않은
    listing이므로 반환하면 안 된다."""
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument)

    symbol = _venue_symbol()
    listed_at = datetime.now(timezone.utc)
    listing = VenueListing(
        instrument_id=instrument.instrument_id,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=listed_at,
        delisted_at=None,
        is_primary=True,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.add_listing(conn, listing)

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_listing(
            conn, Venue.BITGET, symbol, listed_at - timedelta(days=1)
        )
    assert found is None


async def test_get_listing_after_delisted_at_returns_none(pool, repo):
    instrument = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument)

    symbol = _venue_symbol()
    listed_at = datetime.now(timezone.utc) - timedelta(days=10)
    delisted_at = listed_at + timedelta(days=5)
    listing = VenueListing(
        instrument_id=instrument.instrument_id,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=listed_at,
        delisted_at=delisted_at,
        is_primary=True,
    )
    async with pool.acquire() as conn, conn.transaction():
        await repo.add_listing(conn, listing)

    async with pool.acquire() as conn, conn.transaction():
        found = await repo.get_listing(conn, Venue.BITGET, symbol, delisted_at)
    assert found is None


async def test_add_listing_overlapping_period_raises(pool, repo):
    """negative: 같은 (venue, venue_symbol)에 겹치는 기간은 DC-4 EXCLUDE
    제약 위반으로 거부돼야 한다(파이썬 선검사가 아니라 실DB INSERT)."""
    instrument_a = _instrument()
    instrument_b = _instrument()
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument_a)
    async with pool.acquire() as conn, conn.transaction():
        await repo.create(conn, instrument_b)

    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    async with pool.acquire() as conn, conn.transaction():
        await repo.add_listing(
            conn,
            VenueListing(
                instrument_id=instrument_a.instrument_id,
                venue=Venue.BITGET,
                venue_symbol=symbol,
                listed_at=t0,
                delisted_at=None,
                is_primary=True,
            ),
        )

    with pytest.raises(VenueListingOverlapError):
        async with pool.acquire() as conn, conn.transaction():
            await repo.add_listing(
                conn,
                VenueListing(
                    instrument_id=instrument_b.instrument_id,
                    venue=Venue.BITGET,
                    venue_symbol=symbol,
                    listed_at=t0 + timedelta(days=1),
                    delisted_at=None,
                    is_primary=True,
                ),
            )
