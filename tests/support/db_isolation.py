"""Per-module template-clone DB helpers, promoted out of
`tests/integration/eventstore/conftest.py` (ADR-2026-09-26-D Decision 2).

Any `isolated`-tier integration test module -- one that scans a whole time
window (`replay_verify(hours=...)`, `risk_replay --since`) or touches a
global singleton row (`system_safety_state`, `ledger_control`) -- needs a
per-test/per-module clone of the migrated template DB instead of the shared
xdist worker DB, plus a "window is clean" guard before it runs any
assertions. This module carries that clone-then-drop lifecycle and guard so
every such module shares one implementation instead of re-deriving it per
directory (task-8619: FA-15's three `test_replay_verify*.py` modules were the
first three callers; eventstore's own `conftest.py`/`_replay_verify_support.py`
wire them for those tests' specific tables).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from tests.support.db import drop_worker_database, ensure_worker_database


async def clone_isolated_db(template_url: str, clone_id: str) -> str:
    """Clone `template_url` into `clone_id`, ready for a single test/module.
    Pair with `drop_isolated_db(template_url, clone_id)` in the caller's
    fixture teardown -- the caller owns the try/finally, this only wraps the
    two `tests/support/db` calls so every isolated fixture clones the same
    way."""
    return await ensure_worker_database(template_url, clone_id)


async def drop_isolated_db(template_url: str, clone_id: str) -> None:
    await drop_worker_database(template_url, clone_id)


@dataclass(frozen=True)
class WindowCheck:
    """One "no leftover rows in [as_of - hours, as_of)" probe for
    `assert_window_clean`. `query` must select a single `key` column and
    accept `$1` = window start, `$2` = window end (`as_of`); `label` prefixes
    the reported keys (e.g. `"order_events order_ids"`)."""

    label: str
    query: str


async def assert_window_clean(
    pool: asyncpg.Pool,
    *,
    checks: Sequence[WindowCheck],
    as_of: datetime,
    hours: int,
    subject: str = "test",
) -> None:
    """Fail loudly if any `checks` query returns rows inside
    [as_of - hours, as_of) before this `subject` wrote anything. Such rows
    were written by another test module sharing the same clone/worker DB (or
    a dirty template) and would otherwise surface later as an unexplained
    mismatch instead of at the source. The message carries sample keys per
    check so the polluting writer can be traced (grep the key), since the
    database itself does not record which test module wrote a row."""
    start = as_of - timedelta(hours=hours)
    hits: list[str] = []
    async with pool.acquire() as conn:
        for check in checks:
            rows = await conn.fetch(f"{check.query} LIMIT 5", start, as_of)
            if rows:
                hits.append(f"{check.label}={[str(r['key']) for r in rows]}")
    if hits:
        raise AssertionError(
            f"window is not clean before this {subject} ran: "
            + ", ".join(hits)
            + f" (window {start.isoformat()} .. {as_of.isoformat()}). These rows were "
            f"written outside this {subject} -- another {subject} sharing the clone/worker "
            "DB, or a dirty template database -- and would surface later as an unexplained "
            "mismatch. Trace the writer by grepping the sample keys' fixtures/seeders."
        )
