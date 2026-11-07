"""DC-4 venue_listings — half-open interval boundary tests (split from task-23029)

`test_instruments_schema_deepen_2879.py`의 `test_venue_listings_allows_touching_boundary_exactly`를
별도 파일로 분리했다. 이 테스트는 venue_listings의 반열린 구간([listed_at, delisted_at)) 경계값이
정확히 맞닿는 경우(직전 delisted_at == 다음 listed_at) 중복으로 간주되지 않음을 검증한다.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.support.db import create_pool_with_retry


def _asyncpg_dsn() -> str:
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


async def test_venue_listings_allows_touching_boundary_exactly(pool: asyncpg.Pool) -> None:
    """직전 delisted_at과 정확히 같은 시각에 시작하는 구간은 겹치지 않는다
    (half-open 경계 — 위 마이크로초 테스트의 반대쪽 경계)."""
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
    await _insert_listing(
        pool,
        instrument_id=instrument_b,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=boundary,
        delisted_at=None,
    )
