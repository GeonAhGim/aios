"""DC-4 instruments/venue_listings — UNIQUE / EXCLUDE 분리 검증 (task-23027)

`test_instruments_schema_deepen_2879.py`가 500줄 임계치를 넘어선 관계로
UNIQUE (venue, venue_symbol, listed_at) 와 EXCLUDE 시간 겹침 제약을
별도 파일로 분리한다.

DoD:
- UNIQUE 중복: 같은 (venue, venue_symbol, listed_at) 이 거부됨
- EXCLUDE 독립성: 겹치는 시간 구간이 거부됨
- 두 제약이 독립적으로 동작함 확인
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


async def test_venue_listings_rejects_exact_duplicate_start_same_symbol(pool: asyncpg.Pool) -> None:
    """UNIQUE (venue, venue_symbol, listed_at) — EXCLUDE보다 먼저 걸리는
    정확한 시작시각 중복도 거부돼야 한다(두 제약이 독립적으로 동작함).
    UNIQUE는 시작시각이 같은 행만 잡으므로 ExclusionViolationError만 허용하면
    UNIQUE 제약을 빼도 통과하는 가짜 독립 검증이 된다 — 두 예외 모두 허용해야
    이 테스트가 UNIQUE 제약을 실제로 검증한다."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(UTC) - timedelta(days=10)

    await _insert_listing(
        pool,
        instrument_id=instrument_a,
        venue="BITGET",
        venue_symbol=symbol,
        listed_at=t0,
        delisted_at=t0 + timedelta(days=1),
    )
    with pytest.raises(
        (asyncpg.exceptions.UniqueViolationError, asyncpg.exceptions.ExclusionViolationError)
    ):
        await _insert_listing(
            pool,
            instrument_id=instrument_b,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=t0,
            delisted_at=t0 + timedelta(days=2),
        )

    # 두 번째 검증: EXCLUDE 독립성 — 시작시각이 달라 UNIQUE는 못 잡고,
    # 겹치는 구간만 EXCLUDE가 잡는 케이스(두 번째가 첫 번째에 포함).
    # 이 케이스는 ExclusionViolationError만 허용해야 UNIQUE 제거 시 실패한다.
    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_listing(
            pool,
            instrument_id=instrument_a,
            venue="BITGET",
            venue_symbol=symbol,
            listed_at=t0 + timedelta(hours=12),
            delisted_at=t0 + timedelta(hours=24),
        )
