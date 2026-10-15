"""Failure injection for the legacy writer's disposable database lifecycle."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.support import order_service_isolation as isolation


@pytest.mark.parametrize("failure", ["connect", "test", "close", None])
async def test_clone_is_removed_on_every_exit(monkeypatch, failure):
    pool = SimpleNamespace(close=AsyncMock())
    create = AsyncMock(return_value=pool)
    drop = AsyncMock()
    ensure = AsyncMock(return_value="postgresql://test-clone")
    monkeypatch.setattr(isolation, "template_database_url", lambda: "test-template")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")
    monkeypatch.setattr(isolation, "ensure_worker_database", ensure)
    monkeypatch.setattr(isolation, "drop_worker_database", drop)
    monkeypatch.setattr(isolation.asyncpg, "create_pool", create)
    if failure == "connect":
        create.side_effect = RuntimeError("connect")
    if failure == "close":
        pool.close.side_effect = RuntimeError("close")

    async def exercise():
        async with isolation.isolated_order_service_pool() as actual:
            assert actual is pool
            if failure == "test":
                raise RuntimeError("test")

    if failure:
        with pytest.raises(RuntimeError, match=failure):
            await exercise()
    else:
        await exercise()
    ensure.assert_awaited_once_with("test-template", "osvc_gw2")
    drop.assert_awaited_once_with("test-template", "osvc_gw2")
    if failure != "connect":
        pool.close.assert_awaited_once()
