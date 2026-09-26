"""FA-15 -- `scripts/replay_verify.py`'s pre-connect DB connection-pressure gate.

esc-ci-replay_verify.json (13th+ recurrence, task-6754, ND-17 regeneration of the
task-6727 leaf whose commit went unreachable/phantom before landing -- see
task-6754's spec): `replay_verify.py` itself has been hardened 10+ times already
(retry budget, jitter, event-loop policy, pool-leak fix) and `pm/local_ci.py`
separately added a connection-pressure pre-check (task-6743's
`_await_db_capacity`) that waits for `pg_stat_activity` load to clear before
invoking this script -- but that guard lives only on `local_ci.py`'s call site.
`pm/ci_recheck.py`'s "full" mode step list (`("replay_verify", [str(py),
"scripts/replay_verify.py"], 300)`) invokes this script directly as a bare
subprocess with no such pre-check, and every recurrence of this escalation
carries `"mode": "full"` -- i.e. every observed failure has come through
exactly the call path task-6743's fix does not cover. `pm/` is out of this
repo's edit scope (CLAUDE.md), so the fix has to live on this side: duplicating
the same pressure gate inside the script itself protects it regardless of
which caller invokes it, closing the gap without touching `pm/ci_recheck.py`.

Split into its own module (not inlined into `replay_verify.py`) because that
file is already 489 lines against the 500-line file-policy warn threshold
(ADR-2026-09-10-C SS7) and `code-ratchets-baseline.json`'s `loc_over_500` count
(42) is a ratchet -- pushing this file over 500 would raise that count, which
DECISION_GUIDELINES B-2 and CLAUDE.md SS3 both forbid absent an explicit CTO
baseline-raise decision. task-6714's commit made the identical split-for-loc
call for this same file's test coverage.

Mirrors `pm/local_ci.py`'s `_pg_connection_pressure`/`_await_db_capacity`
(task-6743) in spirit: best-effort observation (a probe failure means "unknown
pressure", not "under pressure" -- it must never block a legitimate run), a
fixed retry budget (never unbounded -- DECISION_GUIDELINES B-2), and
fail-open once that budget is exhausted (this is contention mitigation, not a
correctness gate; `replay_verify`'s own fail-closed digest comparison is
untouched by this module either way).
"""

from __future__ import annotations

import random
import sys
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import asyncpg

DB_PRESSURE_THRESHOLD = 0.7
DB_PRESSURE_MAX_RETRIES = 3
DB_PRESSURE_BACKOFF_SEC: tuple[float, ...] = (5.0, 10.0, 20.0)

# task-7877 (esc-ci-replay_verify.json, 16th+ recurrence): `await_db_capacity`
# above only samples an aggregate active/max_connections percentage -- it
# cannot see a `setup_test_db.py --reset` in progress against this exact
# database, because that probe connects to the same (possibly mid-DROP)
# target `dsn` and, on failure, treats the outcome as "unknown" and proceeds
# (task-6754's documented fail-open choice). The actual serialization
# primitive `setup_test_db.py._ensure_database`/`_drop_database` already use
# is a Postgres session advisory lock keyed by
# `hashtextextended(<database name>, 0)`, held for the *entire*
# terminate/DROP/CREATE(/migrate) window -- a signal visible cluster-wide
# (any worktree, any invoker: `pm/local_ci.py` and `pm/ci_recheck.py`'s
# unguarded "full"-mode direct subprocess call alike) by connecting to the
# stable `postgres` maintenance database instead of the target database that
# may itself be mid-recreate. Waiting for that specific lock to clear before
# ever dialing the target database closes the exact race this escalation's
# traceback shows (reset landing inside the initial connect handshake),
# rather than widening `_POOL_CONNECT_ATTEMPTS`/`DB_PRESSURE_*` again
# (DECISION_GUIDELINES B-2) -- this is a new, more precise signal, not a
# bigger number on an existing one.
RESET_LOCK_MAX_RETRIES = 3
RESET_LOCK_BACKOFF_SEC: tuple[float, ...] = (2.0, 4.0, 8.0)

# task-7648 (esc-ci-replay_verify.json, 14th+ recurrence, first_seen 2026-09-22):
# the traceback moved from `proactor_events.py` (task-6522's target) to
# `selector_events.py`'s own `_read_ready__data_received` -- the exact reset shape
# task-6522 set out to eliminate by switching event-loop policy still happens, just
# surfaced through the *other* loop backend's read path. That proves the reset is
# not an artifact of which asyncio loop backend drives the socket -- it is a real
# external TCP RST landing during the initial connect handshake itself, which
# `await_db_capacity` cannot see: every sibling `replay_verify.py` process
# pm/local_ci.py spawns (one per xdist worker DB, task-6743's registration site)
# probes `pg_stat_activity` and dials out at effectively the same instant they were
# all launched, so N processes can each observe "under threshold" independently and
# then all open new sockets in the same instant regardless of what the gate
# measured a moment earlier -- a thundering herd `await_db_capacity`'s own TOCTOU
# cannot close (it guards against *sustained* pressure, not a *simultaneous burst*
# from processes that all checked before any of them connected).
_STARTUP_JITTER_MAX_SEC = 2.0

_PROBE_ERRORS: tuple[type[BaseException], ...] = (OSError, asyncpg.PostgresError)


async def stagger_startup(*, sleep: Callable[[float], Awaitable[None]]) -> None:
    """Sleeps `random.uniform(0, _STARTUP_JITTER_MAX_SEC)` before a
    `replay_verify.py` process makes its first connect attempt -- spreads
    sibling processes launched near-simultaneously (see module docstring)
    across a few hundred ms to a couple of seconds, turning their lockstep
    connect burst into the same trickle the retry backoff already tolerates
    one connection at a time. Randomized (not a fixed delay) so concurrent
    processes computing the same schedule do not just move the herd to a new
    fixed instant -- mirrors `replay_verify._sleep_before_retry`'s identical
    decorrelation rationale (task-6627). No budget or threshold moved here
    (DECISION_GUIDELINES B-2) -- this only changes *when* the existing,
    unchanged retry budget starts spending itself."""
    await sleep(random.uniform(0, _STARTUP_JITTER_MAX_SEC))  # noqa: S311 -- startup jitter, not crypto


async def connection_pressure(dsn: str) -> tuple[int, int] | None:
    """`(active connections, max_connections)` via `pg_stat_activity` /
    `pg_settings`, or `None` if the probe itself fails (network blip,
    insufficient privilege, the DB not existing yet mid-`setup_test_db.py
    --reset`) -- a failed probe is an unknown, not evidence of pressure, so
    callers must treat `None` as "proceed", never as "blocked"."""
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=10)
    except _PROBE_ERRORS:
        return None
    try:
        row = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM pg_stat_activity) AS active, "
            "(SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_conn"
        )
    except _PROBE_ERRORS:
        return None
    finally:
        try:
            await conn.close()
        except _PROBE_ERRORS:
            pass
    if row is None:
        return None
    return int(row["active"]), int(row["max_conn"])


async def await_db_capacity(
    dsn: str,
    *,
    sleep: Callable[[float], Awaitable[None]],
    probe: Callable[[str], Awaitable[tuple[int, int] | None]] = connection_pressure,
) -> None:
    """Waits (up to `DB_PRESSURE_MAX_RETRIES` backoff steps) for
    `active/max_connections` to drop below `DB_PRESSURE_THRESHOLD` before
    returning. Fail-open: an unreadable probe or an exhausted retry budget
    both return normally rather than blocking `replay_verify` indefinitely --
    this is contention mitigation, not a second correctness gate."""
    delays = (0.0, *DB_PRESSURE_BACKOFF_SEC)
    for attempt, delay in enumerate(delays):
        if delay:
            print(
                f"replay_verify: DB connection pressure -- waiting {delay:.0f}s "
                f"(retry={attempt}/{DB_PRESSURE_MAX_RETRIES})",
                file=sys.stderr,
            )
            await sleep(delay)
        pressure = await probe(dsn)
        if pressure is None:
            return
        active, max_conn = pressure
        if max_conn <= 0 or active / max_conn < DB_PRESSURE_THRESHOLD:
            return
        print(
            f"replay_verify: active={active} max_connections={max_conn} "
            f"(threshold={DB_PRESSURE_THRESHOLD})",
            file=sys.stderr,
        )
    print(
        "replay_verify: DB connection pressure persisted past the retry budget -- "
        "proceeding anyway",
        file=sys.stderr,
    )


def target_database_name(dsn: str) -> str:
    """The bare database name `dsn` points at (`urlsplit(dsn).path` minus the
    leading `/`) -- the same name `setup_test_db.py._ensure_database`/
    `_drop_database` key their advisory lock on."""
    return urlsplit(dsn).path.lstrip("/")


def maintenance_dsn(dsn: str) -> str:
    """`dsn` with its database swapped for the `postgres` maintenance
    database -- stable and never itself `DROP`/`CREATE`d by
    `setup_test_db.py`, so probing it stays reachable even while the actual
    target database is mid-recreate."""
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


async def reset_lock_held(dsn: str, database: str) -> bool | None:
    """`True` if `setup_test_db.py`'s advisory lock for `database` is
    currently held elsewhere (a reset/recreate is in progress), `False` if
    free, `None` if the probe itself failed -- treated as unknown, not "not
    held", by the caller. Connects to `maintenance_dsn(dsn)`, not `dsn`
    itself, precisely because `dsn`'s database may be the one being torn
    down. Uses `pg_try_advisory_lock`/`pg_advisory_unlock` (non-blocking) so
    this probe never itself waits on the lock it is only trying to observe."""
    try:
        conn = await asyncpg.connect(dsn=dsn, timeout=10)
    except _PROBE_ERRORS:
        return None
    try:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", database
        )
        if acquired:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", database)
        return not acquired
    except _PROBE_ERRORS:
        return None
    finally:
        try:
            await conn.close()
        except _PROBE_ERRORS:
            pass


async def await_reset_lock_clear(
    dsn: str,
    *,
    sleep: Callable[[float], Awaitable[None]],
    probe: Callable[[str, str], Awaitable[bool | None]] = reset_lock_held,
) -> None:
    """Waits (up to `RESET_LOCK_MAX_RETRIES` backoff steps) for
    `setup_test_db.py`'s reset advisory lock on `target_database_name(dsn)`
    to clear before returning. Fail-open, same as `await_db_capacity`: an
    unreadable probe or an exhausted retry budget both return normally
    rather than blocking `replay_verify` indefinitely -- this is contention
    mitigation, not a correctness gate."""
    database = target_database_name(dsn)
    admin_dsn = maintenance_dsn(dsn)
    delays = (0.0, *RESET_LOCK_BACKOFF_SEC)
    for attempt, delay in enumerate(delays):
        if delay:
            print(
                f"replay_verify: reset lock held for database={database} -- "
                f"waiting {delay:.0f}s (retry={attempt}/{RESET_LOCK_MAX_RETRIES})",
                file=sys.stderr,
            )
            await sleep(delay)
        held = await probe(admin_dsn, database)
        if held is not True:
            return
    print(
        "replay_verify: reset lock persisted past the retry budget -- proceeding anyway",
        file=sys.stderr,
    )
