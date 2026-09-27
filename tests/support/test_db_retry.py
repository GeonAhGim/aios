"""PLT-36/task-8352/esc-ci-coverage: regression tests for the shared
`_RETRYABLE_CONNECT_ERRORS` gap in tests/support/db.py's admin-connect,
clone, and pool-connect retry sites.

Split out of tests/support/test_db.py (ADR-2026-09-10-C §7 file policy:
distinct concern -- retry/reconnect behavior against sibling-worktree DB
resets -- kept out of the general helper coverage file rather than pushing
it over the 500-line warn threshold).
"""

from __future__ import annotations

import sys
from typing import Any

import asyncpg
import pytest

from tests.support.db import (
    _admin_connect_with_retry,
    create_pool_with_retry,
    ensure_worker_database,
)


@pytest.mark.asyncio
async def test_admin_connect_with_retry_recovers_from_invalid_catalog_name() -> None:
    """task-8284/esc-ci-coverage: `InvalidCatalogNameError` ("database ... does
    not exist") is the shape a connect hits when it lands inside a sibling
    worktree's `DROP DATABASE` -> `CREATE DATABASE` window (task-6267/6284).
    Before this fix `_admin_connect_with_retry` only caught
    `(OSError, ConnectionDoesNotExistError)`, so this shape propagated
    unretried on the very first attempt -- it must now retry and recover."""
    attempts = 0

    async def _fake_connect(*args: object, **kwargs: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncpg.exceptions.InvalidCatalogNameError(
                'database "aios_test_gw0" does not exist'
            )
        return "connected"

    db_module7 = sys.modules["tests.support.db"]
    original_connect = asyncpg.connect
    try:
        db_module7.asyncpg.connect = _fake_connect  # pyright: ignore[reportAttributeAccessIssue]
        result = await _admin_connect_with_retry("postgresql://user:pass@localhost:5432/postgres")
    finally:
        db_module7.asyncpg.connect = original_connect

    assert result == "connected"
    assert attempts == 2


@pytest.mark.asyncio
async def test_create_pool_with_retry_recovers_from_cannot_connect_now() -> None:
    """`CannotConnectNowError` ("the database system is starting up") is the
    other shape task-6267/6284 root-caused for the same drop/create window.
    `create_pool_with_retry` must retry and recover instead of propagating
    unretried on the first attempt."""
    attempts = 0

    class _FakePool:
        def __await__(self) -> Any:
            async def _inner() -> _FakePool:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise asyncpg.exceptions.CannotConnectNowError(
                        "the database system is starting up"
                    )
                return self

            return _inner().__await__()

        def terminate(self) -> None:
            pass

    db_module8 = sys.modules["tests.support.db"]
    original_create_pool = asyncpg.create_pool
    try:
        db_module8.asyncpg.create_pool = lambda *a, **k: _FakePool()  # pyright: ignore[reportAttributeAccessIssue]
        pool = await create_pool_with_retry("postgresql://user:pass@localhost:5432/aios_test")
    finally:
        db_module8.asyncpg.create_pool = original_create_pool

    assert isinstance(pool, _FakePool)
    assert attempts == 2


@pytest.mark.asyncio
async def test_ensure_worker_database_reconnects_after_invalid_catalog_name_mid_loop() -> None:
    """Same shape as `test_ensure_worker_database_reconnects_after_transient_reset_mid_loop`
    (tests/support/test_db.py) but with `InvalidCatalogNameError` -- the exact
    error a connect hits when it lands inside a sibling worktree's DROP+CREATE
    window (task-6267/6284). Before this fix, this shape was not in the loop's
    except clause and would have propagated unretried."""
    template_url = "postgresql+asyncpg://user:pass@localhost:5432/aios_test"

    class _FakeConnection:
        def __init__(self, ordinal: int) -> None:
            self.ordinal = ordinal
            self.closed = False

        async def execute(self, sql: str, *args: object, **kwargs: object) -> str:
            if self.ordinal == 1 and "DROP" in sql:
                raise asyncpg.exceptions.InvalidCatalogNameError(
                    'database "aios_test_gw0" does not exist'
                )
            return "0"

        async def close(self) -> None:
            self.closed = True

    db_module9 = sys.modules["tests.support.db"]
    original_connect = asyncpg.connect
    connections: list[_FakeConnection] = []

    async def _fake_connect9(*args: object, **kwargs: object) -> _FakeConnection:
        conn = _FakeConnection(len(connections) + 1)
        connections.append(conn)
        return conn

    try:
        db_module9.asyncpg.connect = _fake_connect9  # pyright: ignore[reportAttributeAccessIssue]
        db_module9._CLONE_RETRY_BASE_DELAY = 0.0  # noqa: SLF001 -- test speed, restored below
        result_url = await ensure_worker_database(template_url, "gw0")
    finally:
        db_module9.asyncpg.connect = original_connect
        db_module9._CLONE_RETRY_BASE_DELAY = 0.2  # noqa: SLF001

    assert "gw0" in result_url
    assert len(connections) == 2, "reset connection is closed and a fresh one opened"
    assert connections[0].closed, "the dead connection from the failed attempt is closed"
    assert connections[1].closed, "the connection that finished the clone is closed on exit"
