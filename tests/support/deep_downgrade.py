"""Purge position snapshots before a migration round trip that descends below FA-4.

FA-0d-fix (task-771991202). `cdb114b6903f` (FA-0d) rewrites `pos_snapshot`
keys 5 -> 4 parts on downgrade and rebuilds the 5th part from the
`portfolio_id` column on upgrade, failing closed (correctly) on any row it
cannot re-key. The snapshot adapter now always fills that column, so a round
trip that stays above FA-4 (`963d5f3cfb1b`) is lossless. A deeper downgrade
is not: FA-4's downgrade drops the column, and FA-2's downgrade drops the
`legal_entity`/`fund`/`portfolio` tables themselves, so on the way back up
the FA-4 backfill has nothing to derive `portfolio_id` from and FA-0d halts
-- leaving the shared test database stuck below head and every later test
failing (the CI red behind 77871f67).

Such a deep downgrade already discards all entity rows in the test database;
discarding the position snapshots that depend on them is the same, explicit
choice. Call `purge_position_snapshots` right before the downgrade in every
test whose target is below FA-4. Nothing references `pos_snapshot` by FK, and
`pos_journal` (WORM, append-only) is untouched.
"""
from __future__ import annotations

import asyncpg


async def purge_position_snapshots(pool: asyncpg.Pool) -> int:
    """Delete every `pos_snapshot` row; returns how many were removed."""
    async with pool.acquire() as conn:
        status = await conn.execute("DELETE FROM pos_snapshot")
    return int(status.rsplit(" ", 1)[-1])
