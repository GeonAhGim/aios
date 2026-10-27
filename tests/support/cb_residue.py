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
import warnings

import asyncpg
import pytest

from tests.support.orders_residue import _TRANSIENT, _can_run_asyncio


async def heal_liquidation_residue(database_url: str, nodeid: str) -> int:
    """Abort non-terminal ``liquidation_request`` rows a finished module left behind.

    ``run_liquidation_worker_once`` advances the *oldest* non-terminal request in the DB, so a
    leftover row from an earlier module made a later test's own request stay REQUESTED (full CI
    90893c2a: ``test_plans_open_position_into_slices``). This heals quietly (a warning names the
    module) instead of failing, because modules that legitimately leave rows are per-worker DB
    harmless to everything except this global-queue worker.
    """
    conn = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if not await conn.fetchval("SELECT to_regclass('liquidation_request') IS NOT NULL"):
            return 0
        rows = await conn.fetch(
            "UPDATE liquidation_request SET state = 'ABORTED', completed_at = now() "
            "WHERE state IN ('REQUESTED', 'PLANNED', 'EXECUTING') RETURNING id"
        )
    finally:
        await conn.close()
    if rows:
        warnings.warn(
            f"liquidation_request residue: {len(rows)} non-terminal row(s) left after {nodeid} "
            "were aborted so later tests on this worker are not shadowed.",
            stacklevel=2,
        )
    return len(rows)


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
        # 2026-10-27: 예전엔 integration/adversarial/e2e 디렉터리만 검사했다. 다른 디렉터리
        # (tests/performance 등)가 halted를 남기면 다음에 검사되는 무고한 모듈이 범인으로
        # 찍혔다(full CI 0def1356). 모듈 경계 검사는 연결 1회로 값싸므로 모든 모듈에서 하고,
        # 남긴 모듈 자신이 잡히게 한다.
        if not hasattr(item, "nodeid"):
            return
        # Check at the module boundary only: circuit-breaker feature tests (test_circuit_breaker.py)
        # intentionally carry level changes across tests inside one file and restore at module end.
        # What breaks *other* modules is a level that survives the file.
        if nextitem is not None and nextitem.path == item.path:
            return
        if not _can_run_asyncio():
            return
        try:
            asyncio.run(heal_liquidation_residue(self.database_url, item.nodeid))
        except _TRANSIENT:
            pass
        try:
            asyncio.run(check_circuit_breaker(self.database_url, item.nodeid))
        except _TRANSIENT:
            pass  # DB unreachable / event-loop conflict: not a sentinel violation
        # AssertionError always propagates (fail-closed).
