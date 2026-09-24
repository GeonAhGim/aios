"""FA-15 -- 1-day replay-vs-current-table verification, wired into local CI.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#9 FA-15.

Usage:
    python scripts/replay_verify.py                     # last 24h, DATABASE_URL
    python scripts/replay_verify.py --hours 24 --as-of 2026-09-07T00:00:00+00:00

Exits 0 if every order/ledger-account touched in the window replays to
exactly the current table row (byte-identical digest, `src.core.eventstore.
replay.digest_state`); exits 1 and prints every mismatch to stderr
otherwise. Fail-closed by construction -- DoD(2) is that this checker can
actually fail, not just report OK (proven in
tests/integration/eventstore/test_replay_verify.py by tampering with a
`ledger_balance` row directly and asserting this script's own exit code).

The window is fixed at 1 day by default (task-2060 decision): the ledger
side folds the *entire* journal from sequence 1 every run (a stream's
current balance can only be known by folding its whole history -- there is
no cheaper "since yesterday" fold), so if journal volume ever makes this
exceed the local-CI budget, move this step to a nightly job with a longer
`--hours` rather than widening the per-commit window (see local_ci.py
registration site for the current wiring).

Positions are intentionally out of scope here (documented gap, not an
oversight): `projections/positions.py` (`snapshot_builder.fold`) needs an
`asset_class` per position that no table stores --
`application/rebuild_snapshot.py`'s docstring carries the identical gap in
production. Wiring it needs an instrument registry this leaf does not have.

Orders' and ledger's projection logic is reused as-is from FA-14
(src/core/eventstore/projections/orders.py,
src/foundation/ledger/domain/eventstore_projection.py -- the latter moved
out of src/core in task-6495 to fix a core-no-io violation -- task-2050
decision) -- not re-implemented here, so "byte-identical" actually proves
the projection and the write path agree.

task-2173 fix: orders whose `order_events` chain does not start at CREATED
are skipped (not counted a mismatch, not a crash) when they predate
`oms_order_transition_cutover.cutover_at` -- 073beca589d5's I6 trigger (`no
status change without an order_events row`) only enforces completeness for
orders created at/after an armed cutover; its own docstring says enforcing
it unconditionally would immediately break order_service/repository.py's
pre-cutover legacy writers. Replaying a pre-cutover order byte-identical is
therefore not a promise this checker can make -- `_order_pair` mirrors the
exact same cutover_at boundary the write-path guard already uses, so this
is not a lenient fold (src/core/eventstore/projections/orders.py's
`EventChainBrokenError` still fires and still fails closed for any order at
or after an armed cutover, where I6 makes a broken chain structurally
impossible).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import asyncpg

from src.core.eventstore import replay
from src.core.eventstore.projections import orders as orders_projection
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.domain import eventstore_projection as ledger_projection
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository

_ORDER_FIELDS = (
    "status",
    "version",
    "filled_quantity",
    "average_fill_price",
    "fee_total",
    "fee_currency",
)

# esc-ci-replay_verify.json: local Windows CI intermittently resets the TCP
# socket to Postgres (WinError 64 / asyncpg ConnectionDoesNotExistError) --
# a transient OS-level reset, not a code regression (bisect landed on an
# unrelated comment-only commit both times because the flake can surface on
# whichever run happens to race it). The first fix (task-6177) only retried
# `asyncpg.create_pool`'s initial handshake; task-6213 is the same reset
# recurring *after* the pool was already up, mid read-only transaction
# (asyncpg's own message for that shape is literally "connection was closed
# in the middle of operation") -- `verify()` holds one connection open for
# the whole scan, so a reset anywhere in that window still needs to unwind
# the (read-only, side-effect-free) transaction and retry the whole scan,
# not just the connect. Retrying both the connect and the scan a few times
# with backoff absorbs the reset wherever it lands without weakening what
# this script actually verifies.
#
# task-6256: esc-ci-replay_verify.json recurred a 4th time *after* task-6177/
# 6213/6236 had already shipped -- the traceback lands back inside
# `_create_pool_with_retry` itself, i.e. all 5 attempts of the old linear
# `0.5*(attempt+1)`s backoff (~5s of sleeping total) were exhausted before
# the reset cleared. Root cause research (docker-compose.dev.yml, tests/
# conftest.py's `retry_too_many_connections`, scripts/setup_test_db.py's
# task-5782/task-5822 notes) confirms every worktree on this machine shares
# one local Postgres container -- a sibling worktree's `setup_test_db.py
# --reset/--drop` runs `pg_terminate_backend` against same-named databases,
# which is a hard kill no client-side timeout can outrun, and can land
# anywhere in a multi-second window depending on what else is running on
# the shared box at that moment. Widening the retry budget is not covering
# for a code bug -- it is sizing the budget to the actual, now-documented
# contention window instead of the arbitrary ~5s picked before that
# evidence existed.
_POOL_CONNECT_ATTEMPTS = 8
_POOL_CONNECT_RETRY_BASE_DELAY: float = 0.5
_POOL_CONNECT_RETRY_MAX_DELAY: float = 8.0

# task-6267: esc-ci-replay_verify.json recurred a 5th time on the exact
# commit that shipped the task-6256 backoff widening -- the traceback still
# lands inside `_create_pool_with_retry`, but that does not mean the budget
# is too small again (DECISION_GUIDELINES B-2: no further budget bump).
# `setup_test_db.py._ensure_database`'s `--reset` path is not just a TCP
# reset -- it is `pg_terminate_backend` -> `DROP DATABASE` -> `CREATE
# DATABASE` against the *same name* `_create_pool_with_retry` is dialing.
# A connect attempt landing inside that drop/create window (not just the
# terminate itself) fails with `InvalidCatalogNameError` ("database ... does
# not exist") or `CannotConnectNowError` ("the database system is
# starting up") -- both are `asyncpg.exceptions.PostgresError`, not
# `OSError` and not `ConnectionDoesNotExistError`, so the old except clause
# let them propagate uncaught on whichever retry attempt happened to race
# that window, discarding every remaining attempt in the budget regardless
# of its size. This was a real gap in what counts as "the same transient
# reset", not the budget being too short -- catching the two additional
# shapes lets the existing backoff actually reach the attempt after the
# sibling worktree's `CREATE DATABASE` lands, instead of aborting early on
# whichever attempt happens to land mid-recreate.
_RETRYABLE_CONNECT_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InvalidCatalogNameError,
    asyncpg.exceptions.CannotConnectNowError,
)


def _retry_delay(attempt: int) -> float:
    """Exponential backoff (`base * 2**attempt`, capped at `_MAX_DELAY`) for
    the `attempt`-th retry (0-indexed) -- a pure function so the schedule is
    unit-testable without `asyncio.sleep`."""
    delay: float = _POOL_CONNECT_RETRY_BASE_DELAY * (2**attempt)
    return min(delay, _POOL_CONNECT_RETRY_MAX_DELAY)


# task-6627: esc-ci-replay_verify.json recurred a 6th time *after* task-6522's
# proactor->selector fix -- the traceback moved from `finish_recv` to
# `selector_events.py`'s `_read_ready__data_received` (still `WinError 10054`
# / `ConnectionDoesNotExistError`), i.e. the reset itself is not an
# IOCP-specific artifact, and the postgres server log shows zero
# connection/FATAL entries for the failing window -- the reset happens below
# Postgres, on the shared machine's loopback TCP stack. task-6256/6267 already
# documented that every worktree on this machine hits the same local Postgres
# concurrently (`setup_test_db.py --reset/--drop`'s `pg_terminate_backend` /
# `DROP DATABASE` / `CREATE DATABASE`); `_retry_delay`'s schedule is
# deterministic, so every worktree's retry loop (replay_verify's own two call
# sites, plus setup_test_db's advisory-lock retries) computes the *same*
# backoff for the *same* attempt number and therefore retries in lockstep --
# a burst of resets desynchronizes each process's attempt counter only
# briefly before they re-converge on the same wall-clock instants, repeatedly
# re-creating the exact contention spike they are backing off from (the
# "thundering herd" that AWS's Exponential-Backoff-and-Jitter writeup
# describes). Retrying with the same deterministic schedule for an 8th time
# is exactly the "widen the budget again" move DECISION_GUIDELINES B-2 rules
# out; jittering *when within the existing cap* each process actually sleeps
# spreads concurrent worktrees' retries across the window instead of pinning
# them to the same instants, without raising `_POOL_CONNECT_ATTEMPTS` or
# `_POOL_CONNECT_RETRY_MAX_DELAY`.
async def _sleep_before_retry(attempt: int) -> None:
    """Full-jitter sleep before the next retry: uniform over
    `[0, _retry_delay(attempt)]` rather than the deterministic delay itself,
    so concurrent processes computing the same schedule do not retry in
    lockstep. `_retry_delay` itself stays a pure, deterministically-tested
    function (its cap and growth are still exactly what
    `test_retry_delay_grows_exponentially_and_caps` asserts) -- only the
    actual `asyncio.sleep` call site is randomized."""
    await asyncio.sleep(random.uniform(0, _retry_delay(attempt)))  # noqa: S311 -- retry jitter, not crypto


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _create_pool_with_retry(dsn: str) -> asyncpg.Pool:
    """`asyncpg.create_pool` with retry on the initial connection only --
    fail-closed still applies: after `_POOL_CONNECT_ATTEMPTS` the original
    exception propagates unchanged, it is never swallowed into a false
    green."""
    for attempt in range(_POOL_CONNECT_ATTEMPTS):
        try:
            return await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        except _RETRYABLE_CONNECT_ERRORS:
            if attempt + 1 >= _POOL_CONNECT_ATTEMPTS:
                raise
            await _sleep_before_retry(attempt)
    raise AssertionError("unreachable -- loop always returns or raises")


def window(as_of: datetime, hours: int) -> tuple[datetime, datetime]:
    """`[as_of - hours, as_of)` -- the streams whose replay gets checked
    this run (which streams, not how much of their history is folded --
    each stream's replay still starts at its own beginning)."""
    return as_of - timedelta(hours=hours), as_of


async def _touched_order_ids(
    conn: asyncpg.Connection, start: datetime, end: datetime
) -> list[UUID]:
    rows = await conn.fetch(
        "SELECT DISTINCT order_id FROM order_events WHERE occurred_at >= $1 AND occurred_at < $2",
        start,
        end,
    )
    return [row["order_id"] for row in rows]


async def _touched_ledger_accounts(
    conn: asyncpg.Connection, start: datetime, end: datetime
) -> list[str]:
    rows = await conn.fetch(
        "SELECT DISTINCT la.account_code FROM ledger_posting_line lpl "
        "JOIN ledger_journal_entry lje ON lje.entry_id = lpl.entry_id "
        "JOIN ledger_account la ON la.account_id = lpl.account_id "
        "WHERE lje.posted_at >= $1 AND lje.posted_at < $2",
        start,
        end,
    )
    return [row["account_code"] for row in rows]


async def _cutover_at(conn: asyncpg.Connection) -> datetime | None:
    value = await conn.fetchval("SELECT cutover_at FROM oms_order_transition_cutover WHERE id = 1")
    return cast(datetime | None, value)


async def _order_pair(
    conn: asyncpg.Connection, order_id: UUID, *, cutover_at: datetime | None
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Replay one order's timeline and diff it against its current row --
    `None` if the order predates `oms_order_transition_cutover.cutover_at`
    and its chain is broken. 073beca589d5's I6 trigger only enforces "no
    status change without an order_events row" for orders created at/after
    an armed cutover (its own docstring: enforcing it unconditionally would
    immediately break order_service/repository.py's pre-cutover legacy
    writers) -- pre-cutover orders were never guaranteed a complete event
    trail, so FA-15 replaying them byte-identical is not a promise this
    system makes. Orders at/after an armed cutover still fail closed (the
    exception propagates) -- for those, I6 means this should be structurally
    impossible."""
    events = await PostgresOrderEventRepository().timeline(conn, order_id)
    fills = await FillsRepository().list_for_order(conn, order_id)
    row = await conn.fetchrow(
        "SELECT created_at, status, version, filled_quantity, average_fill_price, "
        "fee_total, fee_currency FROM orders WHERE order_id = $1",
        order_id,
    )
    try:
        projected = orders_projection.project(order_id, events, fills)
    except orders_projection.EventChainBrokenError as exc:
        pre_cutover = cutover_at is None or row is None or row["created_at"] < cutover_at
        if not pre_cutover:
            raise
        print(
            f"replay_verify: order_id={order_id} skipped -- pre-cutover order, "
            f"event history incomplete (not an FA-16 wiring guarantee): {exc}",
            file=sys.stderr,
        )
        return None
    replayed: dict[str, Any] = {
        "status": projected.status.value,
        "version": projected.version,
        "filled_quantity": projected.filled_quantity,
        "average_fill_price": projected.average_fill_price,
        "fee_total": projected.fee_total,
        "fee_currency": projected.fee_currency,
    }
    actual: dict[str, Any] = {field: None if row is None else row[field] for field in _ORDER_FIELDS}
    return replayed, actual


async def _ledger_pairs(
    conn: asyncpg.Connection,
    journal: PostgresJournalRepository,
    account_codes: list[str],
) -> dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]]:
    if not account_codes:
        return {}
    entries = await journal.list_since(conn, 0)
    balances = ledger_projection.project(entries)
    rows = await conn.fetch(
        "SELECT la.account_code, lb.balance, lb.last_entry_seq FROM ledger_balance lb "
        "JOIN ledger_account la ON la.account_id = lb.account_id "
        "WHERE la.account_code = ANY($1::text[])",
        account_codes,
    )
    actual_by_code = {row["account_code"]: row for row in rows}

    pairs: dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]] = {}
    for code in account_codes:
        balance = balances.get(code)
        replayed: dict[str, Any] = {
            "balance": None if balance is None else balance.balance,
            "last_entry_seq": None if balance is None else balance.last_entry_seq,
        }
        actual_row = actual_by_code.get(code)
        actual: dict[str, Any] = {
            "balance": None if actual_row is None else actual_row["balance"],
            "last_entry_seq": None if actual_row is None else actual_row["last_entry_seq"],
        }
        pairs[("ledger", code)] = (replayed, actual)
    return pairs


async def verify(pool: asyncpg.Pool, *, as_of: datetime, hours: int) -> replay.ReplayReport:
    """Load every stream touched in `window(as_of, hours)`, replay it via
    FA-14's projections, and diff against its current table row.

    `REPEATABLE READ` (read-only) wraps the whole scan in one snapshot --
    without it, the touched-id scan and the later fold-and-compare reads
    (`_order_pair`/`_ledger_pairs`) are separate auto-committed statements,
    so a write landing on an already-scanned stream in between would make
    `actual` reflect it while `replayed` (folded from an earlier read) does
    not, a false mismatch with no real drift behind it. task-2394 found no
    live evidence of this actually firing (a concurrent-writer stress test
    against this function produced zero mismatches), but it costs nothing
    to close a real TOCTOU gap outright rather than leave it to chance."""
    start, end = window(as_of, hours)
    journal = PostgresJournalRepository(pool)
    async with pool.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        order_ids = await _touched_order_ids(conn, start, end)
        account_codes = await _touched_ledger_accounts(conn, start, end)
        cutover_at = await _cutover_at(conn)

        streams: dict[replay.StreamKey, tuple[dict[str, Any], dict[str, Any]]] = {}
        for order_id in order_ids:
            pair = await _order_pair(conn, order_id, cutover_at=cutover_at)
            if pair is not None:
                streams[("orders", str(order_id))] = pair
        streams.update(await _ledger_pairs(conn, journal, account_codes))

    return replay.verify_replay(streams)


async def _verify_with_retry(
    pool: asyncpg.Pool, *, as_of: datetime, hours: int
) -> replay.ReplayReport:
    """`verify()` with retry on a connection reset anywhere in its scan --
    fail-closed still applies: after `_POOL_CONNECT_ATTEMPTS` the original
    exception propagates unchanged, it is never swallowed into a false
    green. Safe to retry wholesale because `verify()`'s transaction is
    read-only (`REPEATABLE READ ... readonly=True`); a reset mid-scan has no
    partial write to roll back, so re-running it from scratch on a fresh
    connection reproduces the exact same read, not a different one.

    task-6284: `pool.acquire()` can dial a brand-new physical connection
    mid-scan too (growing the pool up to `max_size`, or replacing a
    connection `_close_pool_ignoring_reset`-style resets already discarded)
    -- a sibling worktree's `setup_test_db.py --reset` drop/create race can
    land on that dial exactly like it can on `_create_pool_with_retry`'s
    initial connect, raising `InvalidCatalogNameError` /
    `CannotConnectNowError` instead of `ConnectionDoesNotExistError`. Only
    catching the narrower reset shape here left that race able to escape
    this retry and fail the step even though `_create_pool_with_retry`
    already treats it as transient -- `_RETRYABLE_CONNECT_ERRORS` closes
    that asymmetry."""
    for attempt in range(_POOL_CONNECT_ATTEMPTS):
        try:
            return await verify(pool, as_of=as_of, hours=hours)
        except _RETRYABLE_CONNECT_ERRORS:
            if attempt + 1 >= _POOL_CONNECT_ATTEMPTS:
                raise
            await _sleep_before_retry(attempt)
    raise AssertionError("unreachable -- loop always returns or raises")


async def _close_pool_ignoring_reset(pool: asyncpg.Pool) -> None:
    """`pool.close()` after `report` is already computed and fail-closed has
    already run its course -- a Windows TCP reset (WinError 64 /
    `ConnectionDoesNotExistError`) hitting an idle pooled connection during
    teardown has no bearing on the verification result and must not replace
    it.

    task-6302: this previously only swallowed `(OSError,
    ConnectionDoesNotExistError)`, a narrower set than
    `_RETRYABLE_CONNECT_ERRORS` that `_create_pool_with_retry` and
    `_verify_with_retry` already treat as the same transient
    `setup_test_db.py --reset` drop/create race (`InvalidCatalogNameError` /
    `CannotConnectNowError`, task-6267/6284). `pool.close()` can still dial
    out to close pooled connections that were themselves opened moments
    earlier by that race, so it can hit the same two shapes -- and unlike
    the connect/scan sites, there is no retry to fall through to here: an
    uncaught exception from this `finally`-block call replaces an already
    -- successful `report` with a false CI failure. Swallowing the identical
    shape closes that asymmetry instead of widening any budget."""
    try:
        await pool.close()
    except _RETRYABLE_CONNECT_ERRORS:
        pass


async def _run(*, hours: int, as_of: datetime) -> int:
    pool = await _create_pool_with_retry(_asyncpg_dsn())
    try:
        report = await _verify_with_retry(pool, as_of=as_of, hours=hours)
    finally:
        await _close_pool_ignoring_reset(pool)

    start, end = window(as_of, hours)
    print(
        f"replay_verify: window=[{start.isoformat()}, {end.isoformat()}) "
        f"streams={report.streams_checked} combined_digest={report.combined_digest}"
    )
    for diff in report.mismatches:
        print(
            f"MISMATCH {diff.domain}:{diff.key} replayed={diff.replayed_digest} "
            f"actual={diff.actual_digest}",
            file=sys.stderr,
        )
    if not report.ok:
        print(f"replay_verify: FAIL ({len(report.mismatches)} mismatch)", file=sys.stderr)
        return 1
    print("replay_verify: OK")
    return 0


# task-6522 (esc-ci-replay_verify.json, sha 79d515e8b93b): every traceback this
# escalation has ever shown lands in `asyncio\windows_events.py`'s `_loop_reading`
# / `finish_recv` -- those are `ProactorEventLoop`-only internals (overlapped I/O
# via IOCP). task-6177 through task-6302 all treated the reset as opaque and widened
# the retry budget/coverage around it; none of them looked at *why* Windows' default
# proactor loop is the one surfacing it. `WindowsSelectorEventLoopPolicy` drives
# sockets through `select`/`poll` readiness instead of IOCP overlapped recv, which
# does not have this reset-propagation path at all -- it is not a bigger retry
# budget, it is removing the specific mechanism the traceback is coming from
# (DECISION_GUIDELINES B-2: root-cause the regression, do not just widen the
# tolerance around it again). No subprocess use in this script, so the selector
# loop's lack of Windows subprocess support does not apply here.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=int, default=24, help="Replay window size in hours (default: 24)"
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Window end, ISO 8601 (default: now, UTC). Fixing this makes reruns reproducible.",
    )
    args = parser.parse_args()
    as_of = (
        datetime.now(timezone.utc) if args.as_of is None else datetime.fromisoformat(args.as_of)
    )
    return asyncio.run(_run(hours=args.hours, as_of=as_of))


if __name__ == "__main__":
    raise SystemExit(main())
