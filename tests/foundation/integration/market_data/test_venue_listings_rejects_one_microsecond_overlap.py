"""DC-4 instruments/venue_listings — split: test_venue_listings_rejects_one_microsecond_overlap

Split from test_instruments_schema_deepen_2879.py (task-23028).
정확히 1마이크로초 겹치는 구간도 거부된다 — half-open 경계 ([listed_at, delisted_at))
가 소수점 이하에서도 정확함을 증명한다.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from tests.support.db import create_pool_with_retry


def _asyncpg_dsn() -> str:
    import os

    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


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
    listed_at,
    delisted_at,
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


@pytest.fixture
async def pool() -> asyncpg.Pool:
    p = await create_pool_with_retry(_asyncpg_dsn(), min_size=2, max_size=32)
    yield p
    await p.close()


async def test_venue_listings_rejects_one_microsecond_overlap(pool: asyncpg.Pool) -> None:
    """정확히 1마이크로초 겹치는 구간도 거부된다 — half-open 경계
    ([listed_at, delisted_at))가 소수점 이하에서도 정확함을 증명한다."""
    from datetime import UTC, datetime, timedelta

    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(UTC) - timedelta(days=10)
    boundary = t0 + timedelta(days=5)

    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=boundary,
    )
    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_b,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=boundary - timedelta(microseconds=1),
            delisted_at=None,
        )
