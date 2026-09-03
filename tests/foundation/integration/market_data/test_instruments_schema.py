"""DC-4 통합 테스트 — instruments/venue_listings DB 제약(실 DB, TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.2, §4.1, §9.2 DC-4.

이 테스트는 애플리케이션 계층(DC-2 symbol_master의 겹침 검사)을 거치지
않고 raw SQL로 직접 INSERT/UPDATE해 DB 제약 자체가 §4.1의 두 불변조건을
강제함을 단언한다: (1) 같은 (venue, venue_symbol)에 기간이 겹치는
venue_listing은 `EXCLUDE USING gist` 위반으로 거부되고, (2)
`instruments.instrument_id`는 트리거로 UPDATE 자체가 거부된다.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
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
    pool: asyncpg.Pool,
    *,
    instrument_id: str,
    venue: str,
    venue_symbol: str,
    listed_at: datetime,
    delisted_at: datetime | None,
) -> None:
    await pool.execute(
        """
        INSERT INTO venue_listings (
            instrument_id, venue, venue_symbol, listed_at, delisted_at, is_primary
        ) VALUES ($1, $2, $3, $4, $5, TRUE)
        """,
        instrument_id, venue, venue_symbol, listed_at, delisted_at,
    )


async def test_venue_listings_rejects_overlapping_period_same_venue_symbol(pool):
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_listing(
        pool, instrument_id=instrument_a, venue="BITGET", venue_symbol=symbol,
        listed_at=t0, delisted_at=t0 + timedelta(days=5),
    )

    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_listing(
            pool, instrument_id=instrument_b, venue="BITGET", venue_symbol=symbol,
            listed_at=t0 + timedelta(days=2), delisted_at=None,
        )


async def test_venue_listings_rejects_overlap_against_open_ended_listing(pool):
    """`delisted_at IS NULL`(현재 상장 중)인 listing과 겹치는 새 구간도
    거부돼야 한다 — EXCLUDE의 COALESCE(delisted_at, 'infinity')가 이를 보장."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_listing(
        pool, instrument_id=instrument_a, venue="BITGET", venue_symbol=symbol,
        listed_at=t0, delisted_at=None,
    )

    with pytest.raises(asyncpg.exceptions.ExclusionViolationError):
        await _insert_listing(
            pool, instrument_id=instrument_b, venue="BITGET", venue_symbol=symbol,
            listed_at=t0 + timedelta(days=100), delisted_at=None,
        )


async def test_venue_listings_allows_sequential_non_overlapping_periods(pool):
    """심볼 변경 흐름(§3.2): 구 listing을 delisted_at으로 닫고 그 이후
    시점부터 새 listing을 등록하면 성공해야 한다(겹치지 않으므로)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)
    symbol = _venue_symbol()
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_listing(
        pool, instrument_id=instrument_id, venue="BITGET", venue_symbol=symbol,
        listed_at=t0, delisted_at=t0 + timedelta(days=5),
    )
    await _insert_listing(
        pool, instrument_id=instrument_id, venue="BITGET", venue_symbol=symbol,
        listed_at=t0 + timedelta(days=5), delisted_at=None,
    )

    rows = await pool.fetch(
        "SELECT listed_at, delisted_at FROM venue_listings "
        "WHERE instrument_id = $1 ORDER BY listed_at",
        instrument_id,
    )
    assert len(rows) == 2


async def test_venue_listings_allows_overlap_across_different_venue_symbol(pool):
    """EXCLUDE 스코프는 (venue, venue_symbol)이다 — 다른 venue_symbol이면
    같은 기간이 겹쳐도 막히지 않아야 한다(과도하게 넓은 배제 방지 회귀)."""
    instrument_a = _fake_ulid()
    instrument_b = _fake_ulid()
    await _insert_instrument(pool, instrument_a)
    await _insert_instrument(pool, instrument_b)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)

    await _insert_listing(
        pool, instrument_id=instrument_a, venue="BITGET", venue_symbol=_venue_symbol(),
        listed_at=t0, delisted_at=None,
    )
    await _insert_listing(
        pool, instrument_id=instrument_b, venue="BITGET", venue_symbol=_venue_symbol(),
        listed_at=t0, delisted_at=None,
    )


async def test_instruments_instrument_id_is_immutable(pool):
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "UPDATE instruments SET instrument_id = $1 WHERE instrument_id = $2",
            _fake_ulid(), instrument_id,
        )


async def test_instruments_lifecycle_state_update_allowed(pool):
    """트리거는 instrument_id 컬럼만 막는다 — 다른 컬럼 UPDATE(예:
    lifecycle_state 전이)는 정상 동작해야 한다(과도한 배제 방지 회귀)."""
    instrument_id = _fake_ulid()
    await _insert_instrument(pool, instrument_id)

    await pool.execute(
        "UPDATE instruments SET lifecycle_state = 'HALTED' WHERE instrument_id = $1",
        instrument_id,
    )

    row = await pool.fetchrow(
        "SELECT lifecycle_state FROM instruments WHERE instrument_id = $1", instrument_id
    )
    assert row["lifecycle_state"] == "HALTED"
