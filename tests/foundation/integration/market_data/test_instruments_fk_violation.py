"""DC-4 instruments/venue_listings FK — unknown instrument_id rejected.

Split of test_venue_listings_rejects_unknown_instrument_id_fk from
test_instruments_schema_deepen_2879.py to reduce file size and
isolate FK-invariant ownership.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

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


async def test_venue_listings_rejects_unknown_instrument_id_fk(pool: asyncpg.Pool) -> None:
    """instrument_id FK — 존재하지 않는 instrument를 참조하는 listing은
    거부된다(기존 테스트는 항상 미리 instrument를 만들어 이 경로를 안 탔다)."""
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await _insert_listing(
            pool,
            instrument_id=_fake_ulid(),
            venue="BITGET",
            venue_symbol=_venue_symbol(),
            listed_at=datetime.now(UTC),
            delisted_at=None,
        )
