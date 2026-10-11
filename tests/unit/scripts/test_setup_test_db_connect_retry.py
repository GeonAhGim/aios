"""DB preparation must tolerate initial transport resets without replaying DDL."""

from unittest.mock import AsyncMock

import asyncpg
import pytest

from tests.unit.scripts.test_setup_test_db import setup_test_db


@pytest.mark.asyncio
async def test_prepare_recovers_initial_reset(monkeypatch):
    conn = AsyncMock()
    conn.fetchval.return_value = True
    connect = AsyncMock(side_effect=[ConnectionResetError("reset"), conn])
    monkeypatch.setattr(setup_test_db.asyncpg, "connect", connect)
    monkeypatch.setattr(setup_test_db.asyncio, "sleep", AsyncMock())
    assert (
        await setup_test_db._ensure_database(
            "postgresql://localhost/test", "aios_test_probe", reset=False
        )
        is False
    )
    assert connect.await_count == 2
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("reset"),
        asyncpg.ConnectionDoesNotExistError("closed"),
        asyncpg.CannotConnectNowError("starting"),
    ],
)
async def test_connect_exhaustion_preserves_failure(monkeypatch, error):
    connect = AsyncMock(side_effect=error)
    monkeypatch.setattr(setup_test_db.asyncpg, "connect", connect)
    monkeypatch.setattr(setup_test_db.asyncio, "sleep", AsyncMock())
    with pytest.raises(type(error)) as caught:
        await setup_test_db._connect_with_retry("postgresql://localhost/test")
    assert caught.value is error
    assert connect.await_count == 5


@pytest.mark.asyncio
async def test_authentication_failure_is_not_retried(monkeypatch):
    connect = AsyncMock(side_effect=asyncpg.InvalidPasswordError("denied"))
    monkeypatch.setattr(setup_test_db.asyncpg, "connect", connect)
    with pytest.raises(asyncpg.InvalidPasswordError):
        await setup_test_db._connect_with_retry("postgresql://localhost/test")
    assert connect.await_count == 1


@pytest.mark.asyncio
async def test_ddl_failure_is_not_replayed(monkeypatch):
    conn = AsyncMock()
    conn.execute.side_effect = ConnectionResetError("lost during lock")
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr(setup_test_db.asyncpg, "connect", connect)
    with pytest.raises(ConnectionResetError):
        await setup_test_db._ensure_database(
            "postgresql://localhost/test", "aios_test_probe", reset=True
        )
    assert connect.await_count == 1
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_has_no_backoff(monkeypatch):
    conn = AsyncMock()
    connect = AsyncMock(return_value=conn)
    sleep = AsyncMock()
    monkeypatch.setattr(setup_test_db.asyncpg, "connect", connect)
    monkeypatch.setattr(setup_test_db.asyncio, "sleep", sleep)
    assert await setup_test_db._connect_with_retry("postgresql://localhost/test") is conn
    assert connect.await_count == 1
    sleep.assert_not_awaited()
