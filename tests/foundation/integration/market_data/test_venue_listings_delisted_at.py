"""DC-4 venue_listings delisted_at CHECK — split from test_instruments_schema_deepen_2879.

Extracted on 2026-11-07 to satisfy the loc_over_500 ratchet (task-23024).
Tests the CHECK constraint: (delisted_at IS NULL OR delisted_at > listed_at).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.support.db import create_pool_with_retry


def _asyncpg_dsn() -> str:
    import os

    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await create_pool_with_retry(_asyncpg_dsn(), min_size=2, max_size=32)
    yield p
    await p.close()


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _venue_symbol() -> str:
    return f"T{uuid.uuid4().hex[:10].upper()}USDT"


async def _insert_instrument(pool: asyncpg.Pool, instrument_id: str) -> None:
    await pool.execute(
        """
        INSERT INTO instruments (
            instrument_id, asset_class, base, quote, isin, figi,
            tick_size, lot_size, calendar_id, lifecycle_state
        ) VALUES ($1, 'CRYPTO', 'BTC', 'USDT', NULL, NULL, 0.01, 0.0001, '24x7', 'ACTIVE')
        """,
        instrument_id,
    )


async def _insert_listing(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    instrument_id: str,
    venue: str,
    venue_symbol: str,
    listed_at: datetime,
    delisted_at: datetime | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO venue_listings (
            instrument_id, venue, venue_symbol, listed_at, delisted_at, is_primary
        ) VALUES ($1, $2, $3, $4, $5, TRUE)
        """,
        instrument_id,
        venue,
        venue_symbol,
        listed_at,
        delisted_at,
    )


# ---- delisted_at CHECK (D2) ----


async def test_venue_listings_rejects_delisted_at_before_listed_at(pool: asyncpg.Pool) -> None:
    """CHECK (delisted_at IS NULL OR delisted_at > listed_at) — delisted_at < listed_at가
    거부됨을 직접 증명한다."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(UTC) - timedelta(days=1)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=t0,
            delisted_at=t0 - timedelta(microseconds=1),
        )


async def test_venue_listings_rejects_delisted_at_equal_to_listed_at(pool: asyncpg.Pool) -> None:
    """CHECK (delisted_at IS NULL OR delisted_at > listed_at) — 등호 경계값이
    거부됨을 직접 증명한다(기존 테스트는 이 CHECK를 전혀 건드리지 않았다)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    t0 = datetime.now(UTC) - timedelta(days=1)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_id,
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=t0,
            delisted_at=t0,
        )
