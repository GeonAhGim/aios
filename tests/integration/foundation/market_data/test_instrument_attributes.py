"""DC-21 -- `instrument_attributes` migration (`b2927f5a25c2`) + repository, real DB.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Section 9.10 DC-21 DoD: (b) UPDATE/DELETE rejected by WORM trigger, not
bypassable by the table owner; (d) upgrade/downgrade/upgrade round trip.
`subprocess`-driven alembic + DSN handling follow the same pattern as
`tests/integration/foundation/entities/test_migration_fa4_columns.py`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest

from src.foundation.market_data.adapters.postgres_instrument_attributes_repository import (
    PostgresInstrumentAttributesRepository,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "8425d20c192e"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _table_exists(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_name = $1", table
        )
    return row is not None


def _instrument_id() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


async def _seed_instrument(pool: asyncpg.Pool, instrument_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instruments "
            "(instrument_id, asset_class, tick_size, lot_size, calendar_id, lifecycle_state) "
            "VALUES ($1, 'CRYPTO', 0.01, 0.0001, '24x7', 'ACTIVE')",
            instrument_id,
        )


async def test_upgrade_downgrade_upgrade_round_trip(pool):
    assert await _table_exists(pool, "instrument_attributes")

    _run_alembic("downgrade", _DOWN_REVISION)
    assert not await _table_exists(pool, "instrument_attributes")

    _run_alembic("upgrade", "head")
    assert await _table_exists(pool, "instrument_attributes")


async def test_worm_rejects_update(pool):
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '10', $2)",
            instrument_id,
            known_at,
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
            await conn.execute(
                "UPDATE instrument_attributes SET attr_value = '999' "
                "WHERE instrument_id = $1 AND attr_key = 'lot_size' AND known_at = $2",
                instrument_id,
                known_at,
            )


async def test_worm_rejects_delete(pool):
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    known_at = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO instrument_attributes (instrument_id, attr_key, attr_value, known_at) "
            "VALUES ($1, 'lot_size', '10', $2)",
            instrument_id,
            known_at,
        )
        with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
            await conn.execute(
                "DELETE FROM instrument_attributes "
                "WHERE instrument_id = $1 AND attr_key = 'lot_size' AND known_at = $2",
                instrument_id,
                known_at,
            )


async def test_repository_query_returns_value_known_at_or_before_as_of(pool):
    """§9.10 DC-21 DoD (a), against the real table: a correction landed at
    T1 must not leak into a query with as_of strictly before T1."""
    instrument_id = _instrument_id()
    await _seed_instrument(pool, instrument_id)
    repo = PostgresInstrumentAttributesRepository(pool)
    t0 = datetime.now(timezone.utc)
    t1 = t0 + timedelta(seconds=5)

    async with pool.acquire() as conn:
        await repo.record(
            conn, instrument_id=instrument_id, attr_key="lot_size", attr_value="10", known_at=t0
        )
        await repo.record(
            conn, instrument_id=instrument_id, attr_key="lot_size", attr_value="100", known_at=t1
        )

        before_correction = await repo.get_as_of(
            conn, instrument_id, as_of=t0 + timedelta(seconds=1)
        )
        after_correction = await repo.get_as_of(conn, instrument_id, as_of=t1)

    assert before_correction["lot_size"].attr_value == "10"
    assert after_correction["lot_size"].attr_value == "100"
