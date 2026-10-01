"""d4e8f1a29c37 real-DB backfill regression test (task-8890, esc-ci-replay_verify.json).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-15
(replay_verify byte-identical guarantee).

subprocess-launches alembic for the same reason/DSN resolution as FA-0d's
`test_migration_fa0d_position_key_portfolio_id.py`.

Scenarios:
1. An order that took the pre-task-8661 buggy path (plain UPDATE of
   `committed_child_qty` only, no companion `order_events` row, reproduced
   here via raw SQL) gets exactly as many self-loop `CHILD_QTY_COMMITTED`
   rows backfilled as the gap between `orders.version` and what `project()`
   folds from the timeline.
2. `committed_child_qty = 0` (never took the buggy path) is left untouched.
3. An order whose gap is already 0 (already fixed forward) gets zero rows
   appended (idempotent).
4. Failure injection -- one INSERT failing rolls back the whole backfill
   (fail-closed).
5. `downgrade()` always rejects with `Em3ChildQtyBackfillIrreversibleError`
   (WORM (I7) -- no fake rollback).
"""

from __future__ import annotations

import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_event, insert_order

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DOWN_REVISION = "6e2b5965124e"
_REASON_CODE = "EM3_CHILD_SLICE_COMMIT_BACKFILL_TASK8890"


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


def _stamp_past_d4e8f1a29c37() -> None:
    """Puts `alembic_version` at `_DOWN_REVISION` (d4e8f1a29c37's parent) so the
    next `upgrade head` re-exercises d4e8f1a29c37's backfill from a clean slate.

    task-10836: a bare `stamp _DOWN_REVISION` from head does NOT just skip
    d4e8f1a29c37 -- it also skips the real `downgrade()` of every revision
    stacked on top of it (f1a9c6d3e8b2's md_candle/md_tick tenant_id columns,
    fa25b1c9d340's WORM guard trigger retrofit), because `stamp` only moves the
    alembic_version pointer and never calls a migration's `downgrade()`. The
    schema then still physically has those columns/triggers while alembic
    believes it is at `_DOWN_REVISION`, so the following `upgrade head` re-runs
    f1a9c6d3e8b2's `ADD COLUMN tenant_id` on a column that is already there and
    dies with DuplicateColumnError (CI full 32f7cc56 red). Running a real
    `downgrade` to d4e8f1a29c37 first executes fa25b1c9d340's and
    f1a9c6d3e8b2's `downgrade()` for real, then `stamp` only needs to skip the
    one revision (d4e8f1a29c37) whose `downgrade()` unconditionally raises.
    """
    _run_alembic_ok("downgrade", "d4e8f1a29c37")
    _run_alembic_ok("stamp", _DOWN_REVISION)


async def _bump_committed_child_qty_without_event(
    pool: asyncpg.Pool, order_id: UUID, *, qty: Decimal
) -> None:
    """Reproduces the pre-task-8661 buggy path -- a plain UPDATE of
    `committed_child_qty` with no companion `order_events` row.
    `oms_enforce_order_transition_trg`'s I5 (unconditional version bump)
    still fires, but I6 (companion event required) doesn't because `status`
    doesn't change -- exactly the gap this migration fixes."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE orders SET committed_child_qty = $2 WHERE order_id = $1", order_id, qty
        )


async def _order_events_count(pool: asyncpg.Pool, order_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )


async def _order_version(pool: asyncpg.Pool, order_id: UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)


async def test_backfill_inserts_self_loop_event_per_unaccounted_version_gap(pool):
    user_id = await create_test_user(pool)
    _stamp_past_d4e8f1a29c37()
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, quantity=Decimal("10"))
    try:
        # Two buggy plain-UPDATE bumps -> version ahead of the (empty) event
        # timeline by 2, gap = 2. quantity=10 keeps both bumps within
        # ck_orders_committed_child_qty_bounds (committed_child_qty <= quantity).
        await _bump_committed_child_qty_without_event(pool, order_id, qty=Decimal("1"))
        await _bump_committed_child_qty_without_event(pool, order_id, qty=Decimal("2"))
        version_before = await _order_version(pool, order_id)
        assert version_before == 2
        assert await _order_events_count(pool, order_id) == 0

        _run_alembic_ok("upgrade", "head")

        async with pool.acquire() as conn:
            events = await conn.fetch(
                "SELECT event, reason_code, from_status, to_status FROM order_events "
                "WHERE order_id = $1 ORDER BY seq",
                order_id,
            )
        assert len(events) == 2
        for row in events:
            assert row["event"] == "CHILD_QTY_COMMITTED"
            assert row["reason_code"] == _REASON_CODE
            assert row["from_status"] == row["to_status"] == "CREATED"
    finally:
        pass  # order_events is WORM (I7) -- the backfilled rows this test
        # asserts on cannot be deleted, and `orders` still has the FK
        # pointing at them; left as permanent residue, same as FA-4's
        # ledger_journal_entry precedent (_fa4_worm_support.py).


async def test_negative_untouched_order_with_zero_committed_qty_gets_no_rows(pool):
    user_id = await create_test_user(pool)
    _stamp_past_d4e8f1a29c37()
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
    try:
        # committed_child_qty stays at its column default (0) -- never went
        # through set_committed_child_qty, so it must be excluded from scope
        # even though its version could drift for unrelated reasons.
        async with pool.acquire() as conn:
            await conn.execute("UPDATE orders SET status = 'CREATED' WHERE order_id = $1", order_id)
        assert await _order_version(pool, order_id) == 1
        assert await _order_events_count(pool, order_id) == 0

        _run_alembic_ok("upgrade", "head")

        assert await _order_events_count(pool, order_id) == 0
    finally:
        # No order_events row was ever inserted for this order (that's the
        # assertion under test), so the FK from order_events to orders never
        # attaches and this delete is safe -- unlike the other tests below.
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM orders WHERE order_id = $1", order_id)


async def test_negative_already_accounted_gap_is_idempotent_no_rows(pool):
    user_id = await create_test_user(pool)
    _stamp_past_d4e8f1a29c37()
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
    try:
        # Fixed-forward path (task-8661): companion event written alongside
        # the committed_child_qty bump -- gap is already 0.
        async with pool.acquire() as conn:
            await insert_event(
                conn,
                order_id,
                from_status="CREATED",
                to_status="CREATED",
                event="CHILD_QTY_COMMITTED",
            )
        await _bump_committed_child_qty_without_event(pool, order_id, qty=Decimal("1"))
        assert await _order_version(pool, order_id) == 1
        assert await _order_events_count(pool, order_id) == 1

        _run_alembic_ok("upgrade", "head")

        assert await _order_events_count(pool, order_id) == 1
    finally:
        pass  # order_events is WORM (I7); see the first test's finally block.


async def test_failure_injection_insert_error_rolls_back_whole_backfill(pool):
    user_id = await create_test_user(pool)
    _stamp_past_d4e8f1a29c37()
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
    trigger_name = "d4e8f1_inject_" + uuid4().hex
    try:
        await _bump_committed_child_qty_without_event(pool, order_id, qty=Decimal("1"))
        async with pool.acquire() as conn:
            await conn.execute(
                f"CREATE FUNCTION {trigger_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN IF NEW.order_id = '{order_id}'::uuid THEN "
                "RAISE EXCEPTION 'd4e8f1_injected_insert_failure'; END IF; RETURN NEW; END $$"
            )
            await conn.execute(
                f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON order_events "
                f"FOR EACH ROW EXECUTE FUNCTION {trigger_name}()"
            )
        result = _run_alembic("upgrade", "head")
        assert result.returncode != 0
        assert "d4e8f1_injected_insert_failure" in result.stdout + result.stderr

        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            assert version == _DOWN_REVISION
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
                )
                == 0
            )
            await conn.execute(f"DROP TRIGGER {trigger_name} ON order_events")
            await conn.execute(f"DROP FUNCTION {trigger_name}()")

        _run_alembic_ok("upgrade", "head")
        assert await _order_events_count(pool, order_id) == 1
    finally:
        # Injection trigger/function are ordinary DDL objects (not WORM) --
        # drop them. The order_events row the retried upgrade wrote is WORM
        # (I7); see the first test's finally block.
        async with pool.acquire() as conn:
            await conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON order_events")
            await conn.execute(f"DROP FUNCTION IF EXISTS {trigger_name}()")


async def test_negative_downgrade_always_raises_irreversible_error(pool):
    # task-10836: head now sits 2 revisions above d4e8f1a29c37
    # (f1a9c6d3e8b2, fa25b1c9d340). A multi-step `downgrade _DOWN_REVISION`
    # from head runs all 3 steps inside one alembic transaction, so when the
    # 3rd step (d4e8f1a29c37's own downgrade) raises, the whole transaction
    # -- including the first 2 steps' otherwise-reversible downgrades --
    # rolls back and `alembic_version` lands back on head, not on
    # d4e8f1a29c37 as this test expects. Downgrade for real to d4e8f1a29c37
    # first (exercising the 2 reversible steps on their own), then attempt
    # only the single remaining step that must raise.
    _run_alembic_ok("downgrade", "d4e8f1a29c37")
    result = _run_alembic("downgrade", _DOWN_REVISION)
    try:
        assert result.returncode != 0
        assert "Em3ChildQtyBackfillIrreversibleError" in result.stdout + result.stderr
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version == "d4e8f1a29c37"
    finally:
        _run_alembic_ok("upgrade", "head")
