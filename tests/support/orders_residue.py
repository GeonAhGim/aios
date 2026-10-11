"""Check the shared worker database outside fixture setup/teardown lifetimes."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import asyncpg
import pytest

from scripts import replay_verify


async def check_orders(database_url: str, boundary: str) -> None:
    pool = await asyncpg.create_pool(
        database_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1, max_size=1,
    )
    try:
        report = await replay_verify.verify(pool, as_of=datetime.now(timezone.utc), hours=24)
        mismatches = [m for m in report.mismatches if m.domain == "orders"]
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
            asyncio.run(check_orders(self.database_url, "worker baseline before " + item.nodeid))
            self.started = True

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_teardown(self, item, nextitem):
        yield
        # The outer hook resumes after *all* applicable fixture finalizers,
        # including module and session finalizers on the last item.
        if nextitem is None or nextitem.path != item.path:
            boundary = "module/session teardown after " + item.nodeid
            asyncio.run(check_orders(self.database_url, boundary))
        elif os.environ.get("AIOS_ORDERS_SENTINEL_EACH_TEST") == "1":
            asyncio.run(check_orders(self.database_url, "test teardown after " + item.nodeid))
