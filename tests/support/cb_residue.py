"""Module-boundary circuit-breaker residue sentinel (task-19018 follow-up, CTO 2026-10-23).

A test that leaves ``system_safety_state.circuit_breaker_level`` at anything but
``normal`` makes every later test in the same xdist worker fail with
``RISK_CIRCUIT_BREAKER_HALTED`` (10 unrelated failures in the 2026-10-23 full run).
This sentinel names the culprit test, and resets the level so later tests are safe.

It uses its own short-lived connection from a plain pytest hook, exactly like
``orders_residue`` -- never ``request.getfixturevalue`` of an async fixture from inside an
async autouse fixture (that raises ``Runner.run() cannot be called from a running event
loop`` at setup and broke every integration test that uses ``pool``).
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest

from tests.support.orders_residue import _TRANSIENT, _can_run_asyncio

_DB_TOUCHING_DIRS = ("integration", "adversarial", "e2e")


def _db_item(item: pytest.Item) -> bool:
    parts = {p.lower() for p in item.path.parts}
    return any(d in parts for d in _DB_TOUCHING_DIRS)


async def check_circuit_breaker(database_url: str, nodeid: str) -> None:
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if not await conn.fetchval("SELECT to_regclass('system_safety_state') IS NOT NULL"):
            return
        level = await conn.fetchval(
            "SELECT circuit_breaker_level FROM system_safety_state WHERE id = 1"
        )
        if level is None or level == "normal":
            return
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    finally:
        await conn.close()
    raise AssertionError(
        f"Sentinel: circuit_breaker_level left at {level!r} after {nodeid} -- the test must "
        "restore it (reset to normal here to protect later tests)."
    )


class CircuitBreakerResidueSentinel:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @pytest.hookimpl(hookwrapper=True, trylast=True)
    def pytest_runtest_teardown(self, item, nextitem):
        yield
        if not hasattr(item, "nodeid") or not _db_item(item):
            return
        # Check at the module boundary only: circuit-breaker feature tests (test_circuit_breaker.py)
        # intentionally carry level changes across tests inside one file and restore at module end.
        # What breaks *other* modules is a level that survives the file.
        if nextitem is not None and nextitem.path == item.path:
            return
        if not _can_run_asyncio():
            return
        try:
            asyncio.run(check_circuit_breaker(self.database_url, item.nodeid))
        except _TRANSIENT:
            pass  # DB unreachable / event-loop conflict: not a sentinel violation
        # AssertionError always propagates (fail-closed).
