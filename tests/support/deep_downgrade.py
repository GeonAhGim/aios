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

from collections.abc import Callable

_EM3_CHILD_QTY_BACKFILL_REVISION = "d4e8f1a29c37"
_EM3_CHILD_QTY_BACKFILL_PARENT_REVISION = "6e2b5965124e"


async def purge_position_snapshots(pool: object) -> int:
    """Delete every `pos_snapshot` row; returns how many were removed."""
    async with pool.acquire() as conn:
        status = await conn.execute("DELETE FROM pos_snapshot")
    return int(status.rsplit(" ", 1)[-1])


def downgrade_past_irreversible_em3_backfill(
    run_alembic: Callable[..., object], target_revision: str
) -> None:
    """Real-downgrade to `target_revision` when it sits below `d4e8f1a29c37`.

    task-10836: `d4e8f1a29c37`'s `downgrade()` unconditionally raises
    `Em3ChildQtyBackfillIrreversibleError` (the compensating `order_events`
    self-loop rows it backfills are WORM (I7) and cannot be un-written --
    review-migration checklist item 8 forbids dropping the append-only
    trigger to fake a rollback). A bare `run_alembic("downgrade",
    target_revision)` from anywhere above it therefore always dies partway
    through, and a bare `stamp` straight to `target_revision` skips every
    revision stacked above `d4e8f1a29c37` for real (including
    `f1a9c6d3e8b2`'s md_candle/md_tick `tenant_id` columns), leaving the
    schema physically ahead of what `alembic_version` claims and the next
    `upgrade head` dying on `DuplicateColumnError` (CI full 32f7cc56 red).
    The fix: downgrade for real down to `d4e8f1a29c37` (so every revision
    above it runs its genuine `downgrade()`), `stamp` past only the one
    revision that cannot be downgraded for real, then continue downgrading
    for real to the actual target below it.
    """
    run_alembic("downgrade", _EM3_CHILD_QTY_BACKFILL_REVISION)
    run_alembic("stamp", _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION)
    run_alembic("downgrade", target_revision)


# ---------------------------------------------------------------------------
# Negative tests (3+) — invariant-violating inputs are explicitly rejected
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Failure-injection tests (1+) — monkeypatch dependency exceptions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Positive smoke test — valid row count is returned correctly
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Invariant guard — SQL injection attempt on table name
# ---------------------------------------------------------------------------
