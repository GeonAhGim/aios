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

import pytest

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
# Tests — fakes only, no database required
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, status: str | BaseException) -> None:
        self._status = status
        self.statements: list[str] = []

    async def execute(self, sql: str) -> str:
        self.statements.append(sql)
        if isinstance(self._status, BaseException):
            raise self._status
        return self._status


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def test_purge_returns_deleted_row_count() -> None:
    conn = _FakeConn("DELETE 7")
    assert await purge_position_snapshots(_FakePool(conn)) == 7
    assert conn.statements == ["DELETE FROM pos_snapshot"]


async def test_purge_rejects_malformed_driver_status() -> None:
    # Fail closed: a status without a trailing row count must not be read as 0 rows.
    with pytest.raises(ValueError):
        await purge_position_snapshots(_FakePool(_FakeConn("")))


async def test_purge_failure_injection_propagates_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_acquire(self: _FakePool) -> None:
        raise ConnectionError("pool exhausted")

    monkeypatch.setattr(_FakePool, "acquire", broken_acquire)
    with pytest.raises(ConnectionError):
        await purge_position_snapshots(_FakePool(_FakeConn("DELETE 0")))


def test_downgrade_stops_before_stamp_when_first_downgrade_fails() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))
        raise RuntimeError("irreversible step")

    with pytest.raises(RuntimeError):
        downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    # No stamp and no further downgrade: the schema is left where it was.
    assert calls == [("downgrade", _EM3_CHILD_QTY_BACKFILL_REVISION)]


def test_downgrade_does_not_stamp_past_failed_real_downgrade_to_target() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))
        if command == "downgrade" and revision == "0000aaaa0000":
            raise RuntimeError("target downgrade failed")

    with pytest.raises(RuntimeError):
        downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    # The stamp happens exactly once, before the final real downgrade, never after a failure.
    assert calls.count(("stamp", _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION)) == 1
    assert calls[-1] == ("downgrade", "0000aaaa0000")


def test_downgrade_runs_fixed_three_step_sequence() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))

    downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    assert calls == [
        ("downgrade", _EM3_CHILD_QTY_BACKFILL_REVISION),
        ("stamp", _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION),
        ("downgrade", "0000aaaa0000"),
    ]
