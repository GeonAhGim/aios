"""f1a9c6d3e8b2 real-DB round trip (task-10836, CI full 32f7cc56 red).

Spec: docs/audits/AUDIT_2026-10-01_data_ingest_replay.md Section 2 F1(M).
subprocess-driven alembic + DSN handling follow the same pattern as
`tests/integration/foundation/entities/test_migration_fa4_columns.py` and
`tests/integration/foundation/market_data/test_instrument_attributes.py`.

`md_candle`/`md_tick` are declaratively partitioned (`PARTITION BY RANGE`,
see `4a1d0c0de008`), so `ADD COLUMN`/`DROP COLUMN` on the parent must cascade
to every existing partition -- this suite checks a partition child
explicitly, not just the parent relation, since Postgres native partitioning
is the one case where a column's presence on the parent does not by itself
prove it is present (or absent) on a child created before the ALTER.

task-10836 root cause: f1a9c6d3e8b2's own `upgrade()`/`downgrade()` are
symmetric. The CI red was `tests/foundation/integration/positions/
test_migration_fa0d_position_key_portfolio_id.py` and `tests/integration/oms/
test_migration_d4e8f1a29c37_child_qty_committed_backfill.py` using
`alembic stamp` to jump straight from `head` down past three revisions
(fa25b1c9d340, f1a9c6d3e8b2, d4e8f1a29c37) to skip only d4e8f1a29c37's
always-raising `downgrade()` -- `stamp` never executes a migration's
`downgrade()`, so f1a9c6d3e8b2's `ADD COLUMN tenant_id` stayed physically in
place while `alembic_version` pointed below it, and the next `upgrade head`
re-ran `ADD COLUMN` into a column that was already there
(`DuplicateColumnError`). Fixed in both files by running a real `downgrade`
down to d4e8f1a29c37 first (so f1a9c6d3e8b2's downgrade actually executes),
then stamping past only the one revision that cannot be downgraded for real.
This file adds the direct regression coverage for f1a9c6d3e8b2 itself that
the migration-chain gate had no leaf-local test to catch (see note below).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.db_schema.conftest import insert_audit_event

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "d4e8f1a29c37"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )


def _run_alembic_ok(*args: str) -> None:
    result = _run_alembic(*args)
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
    _run_alembic_ok("upgrade", "head")
    yield
    _run_alembic_ok("upgrade", "head")


async def _column_exists(pool: asyncpg.Pool, table: str, column: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name = $1 AND column_name = $2",
            table,
            column,
        )
    return row is not None


async def _index_exists(pool: asyncpg.Pool, index_name: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = $1", index_name)
    return row is not None


async def _one_existing_partition(pool: asyncpg.Pool, parent: str) -> str:
    """Returns the name of one physical partition child of `parent` -- native
    partitioning requires checking a child directly, see module docstring."""
    async with pool.acquire() as conn:
        child = await conn.fetchval(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = $1 LIMIT 1",
            parent,
        )
    assert child is not None, f"{parent} has no partition child -- fixture setup is broken"
    return child


async def _insert_instrument(pool: asyncpg.Pool) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO md_instrument "
            "(venue, canonical_symbol, venue_symbol, asset_class, tick_size, lot_size, "
            " status, listed_at) "
            "VALUES ('BITGET', $1, $1, 'CRYPTO', 0.01, 0.0001, 'LISTED', now()) "
            "RETURNING instrument_id",
            f"TEST-{uuid.uuid4().hex}",
        )


async def _insert_batch(pool: asyncpg.Pool, instrument_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        audit_event_id = await insert_audit_event(conn)
        return await conn.fetchval(
            "INSERT INTO md_ingest_batch "
            "(tenant_id, source, venue, instrument_id, timeframe, range_start, range_end, "
            " request_fingerprint, batch_hash, verdict, audit_event_id) "
            "VALUES (NULL, 'test', 'BITGET', $1, '1m', now(), now(), $2, $3, 'ACCEPT', $4) "
            "RETURNING id",
            instrument_id,
            f"fp-{uuid.uuid4().hex}",
            f"hash-{uuid.uuid4().hex}",
            audit_event_id,
        )


async def _insert_candle(
    pool: asyncpg.Pool, *, instrument_id: UUID, batch_id: UUID, tenant_id: UUID | None
) -> None:
    open_time = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO md_candle "
            "(venue, instrument_id, timeframe, open_time, close_time, "
            " open, high, low, close, volume, batch_id, tenant_id) "
            "VALUES ('BITGET', $1, '1m', $2, $3, 100, 110, 90, 105, 10, $4, $5)",
            instrument_id,
            open_time,
            open_time + timedelta(minutes=1),
            batch_id,
            tenant_id,
        )


async def _insert_tick(pool: asyncpg.Pool, *, instrument_id: UUID, tenant_id: UUID | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO md_tick "
            "(venue, instrument_id, trade_id, price, quantity, side, traded_at, tenant_id) "
            "VALUES ('BITGET', $1, $2, 100, 1, 'buy', now(), $3)",
            instrument_id,
            f"trade-{uuid.uuid4().hex}",
            tenant_id,
        )


async def test_upgrade_downgrade_upgrade_round_trip_no_data(pool):
    """D3 round trip, empty tables -- parent *and* a partition child must
    both lose `tenant_id`/its index on downgrade and regain them on
    re-upgrade; the FK to `tenant(id)` must also come back."""
    candle_child = await _one_existing_partition(pool, "md_candle")
    tick_child = await _one_existing_partition(pool, "md_tick")

    assert await _column_exists(pool, "md_candle", "tenant_id")
    assert await _column_exists(pool, "md_tick", "tenant_id")
    assert await _column_exists(pool, candle_child, "tenant_id")
    assert await _column_exists(pool, tick_child, "tenant_id")
    assert await _index_exists(pool, "idx_md_candle_tenant_id")
    assert await _index_exists(pool, "idx_md_tick_tenant_id")

    _run_alembic_ok("downgrade", _DOWN_REVISION)

    assert not await _column_exists(pool, "md_candle", "tenant_id")
    assert not await _column_exists(pool, "md_tick", "tenant_id")
    assert not await _column_exists(pool, candle_child, "tenant_id")
    assert not await _column_exists(pool, tick_child, "tenant_id")
    assert not await _index_exists(pool, "idx_md_candle_tenant_id")
    assert not await _index_exists(pool, "idx_md_tick_tenant_id")

    _run_alembic_ok("upgrade", "head")

    assert await _column_exists(pool, "md_candle", "tenant_id")
    assert await _column_exists(pool, "md_tick", "tenant_id")
    assert await _column_exists(pool, candle_child, "tenant_id")
    assert await _column_exists(pool, tick_child, "tenant_id")
    assert await _index_exists(pool, "idx_md_candle_tenant_id")
    assert await _index_exists(pool, "idx_md_tick_tenant_id")


async def test_upgrade_downgrade_upgrade_round_trip_with_data(pool):
    """D3 round trip with rows present on both the WORM (`md_candle`) and
    non-WORM (`md_tick`) side -- `DROP COLUMN tenant_id` on downgrade must
    succeed even though rows exist (it drops data, not the row), and the
    re-upgrade must not choke on pre-existing rows without the column."""
    tenant_id = await create_test_tenant(pool)
    instrument_id = await _insert_instrument(pool)
    batch_id = await _insert_batch(pool, instrument_id)
    await _insert_candle(pool, instrument_id=instrument_id, batch_id=batch_id, tenant_id=tenant_id)
    await _insert_tick(pool, instrument_id=instrument_id, tenant_id=tenant_id)

    async with pool.acquire() as conn:
        # Scoped to this test's own instrument_id, not the whole table --
        # under xdist (-n 4) the table is shared with concurrently-running
        # tests that insert their own rows (task-10836).
        candle_count_before = await conn.fetchval(
            "SELECT count(*) FROM md_candle WHERE instrument_id = $1", instrument_id
        )
        tick_count_before = await conn.fetchval(
            "SELECT count(*) FROM md_tick WHERE instrument_id = $1", instrument_id
        )
    assert candle_count_before == 1
    assert tick_count_before == 1

    _run_alembic_ok("downgrade", _DOWN_REVISION)

    assert not await _column_exists(pool, "md_candle", "tenant_id")
    assert not await _column_exists(pool, "md_tick", "tenant_id")
    async with pool.acquire() as conn:
        # Rows survive -- only the column is dropped, not the data.
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM md_candle WHERE instrument_id = $1", instrument_id
            )
            == candle_count_before
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM md_tick WHERE instrument_id = $1", instrument_id
            )
            == tick_count_before
        )

    _run_alembic_ok("upgrade", "head")

    assert await _column_exists(pool, "md_candle", "tenant_id")
    assert await _column_exists(pool, "md_tick", "tenant_id")
    async with pool.acquire() as conn:
        candle_tenant = await conn.fetchval(
            "SELECT tenant_id FROM md_candle WHERE instrument_id = $1", instrument_id
        )
        tick_tenant = await conn.fetchval(
            "SELECT tenant_id FROM md_tick WHERE instrument_id = $1", instrument_id
        )
    # Re-ADD COLUMN always starts NULL -- the pre-downgrade tenant_id value
    # does not and cannot survive a real DROP COLUMN, unlike row survival above.
    assert candle_tenant is None
    assert tick_tenant is None
