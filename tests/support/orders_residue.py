"""Check the shared worker database outside fixture setup/teardown lifetimes."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import asyncpg
import pytest

from scripts import replay_verify


def _can_run_asyncio() -> bool:
    """Return True when asyncio.run() is safe to call right now."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # No current loop — safe.
        return True
    return not loop.is_running()


# Exceptions that indicate a transient/environmental failure, not a real
# sentinel violation.  AssertionError (and its subclasses) always propagate
# so that tests fail-closed when the sentinel detects dirty state.
_TRANSIENT = (
    RuntimeError,  # event-loop conflict (xdist / asyncio)
    ConnectionError,  # DB unreachable
    OSError,  # network / socket level
)


async def check_orders(database_url: str, boundary: str) -> None:
    pool = await asyncpg.create_pool(
        database_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1,
        max_size=1,
    )
    try:
        report = await replay_verify.verify(pool, as_of=datetime.now(timezone.utc), hours=24)
        mismatches = [m for m in report.mismatches if m.domain == "orders"]
        # Event-window enumeration cannot discover a row with no events at all.
        # Use the same projection and comparison fields, with no cutover exception.
        async with (
            pool.acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            rows = await conn.fetch(
                "SELECT order_id FROM orders o WHERE NOT EXISTS "
                "(SELECT 1 FROM order_events e WHERE e.order_id = o.order_id)"
            )
            for row in rows:
                pair = await replay_verify._order_pair(conn, row["order_id"], cutover_at=None)
                if pair is not None and pair[0] != pair[1]:
                    raise AssertionError(
                        f"Sentinel: eventless orders residue at {boundary}: {row['order_id']}"
                    )
        if mismatches:
            raise AssertionError(f"Sentinel: orders residue at {boundary}: {mismatches}")
    finally:
        await pool.close()


class OrdersResidueSentinel:
    def __init__(self, database_url: str) -> None:
        # Capture before fixtures can redirect DATABASE_URL to disposable clones.
        self.database_url = database_url
        self.started = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        if not self.started:
            # Guard: skip if item is not a proper pytest Item (e.g., coroutine from async test
            # collected under asyncio_mode="auto" — item.nodeid would raise AttributeError).
            if not hasattr(item, "nodeid"):
                return
            if _can_run_asyncio():
                try:
                    asyncio.run(
                        check_orders(self.database_url, "worker baseline before " + item.nodeid)
                    )
                except _TRANSIENT:
                    # event loop already running (pytest-xdist / pytest-asyncio),
                    # or DB unreachable — skip baseline.
                    pass
                # AssertionError always propagates (fail-closed).
            # else: event loop already running (pytest-xdist / pytest-asyncio);
            # skip baseline check — the loop is managed by the framework.
            self.started = True

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        yield
        # The outer hook resumes after *all* applicable fixture finalizers,
        # including module and session finalizers on the last item.
        if nextitem is None or nextitem.path != item.path:
            boundary = "module/session teardown after " + item.nodeid
            if _can_run_asyncio():
                try:
                    asyncio.run(check_orders(self.database_url, boundary))
                except _TRANSIENT:
                    # event loop already running (pytest-xdist / pytest-asyncio),
                    # or DB unreachable — skip teardown check.
                    pass
                # AssertionError always propagates (fail-closed).
            # else: event loop already running — skip teardown check.
        elif os.environ.get("AIOS_ORDERS_SENTINEL_EACH_TEST") == "1":
            if _can_run_asyncio():
                try:
                    asyncio.run(
                        check_orders(self.database_url, "test teardown after " + item.nodeid)
                    )
                except _TRANSIENT:
                    pass
                # AssertionError always propagates (fail-closed).
