"""RD-20 통합테스트 공용 픽스처 — `tests/integration/foundation/market_data/conftest.py`와
동일 패턴(`TEST_DATABASE_URL` -> asyncpg pool)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    parts = urlsplit(url)
    if parts.scheme not in {"postgresql+asyncpg", "postgresql", "postgres"}:
        raise ValueError("A PostgreSQL test database URL is required")
    if not parts.hostname or not parts.path.strip("/"):
        raise ValueError("An explicit test database host and name are required")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    try:
        yield p
    finally:
        await p.close()


# These tests also have a normal-discovery entry point in test_pool_fixture.py.
@pytest.mark.parametrize("url", ["", "sqlite:///test.db", "postgresql://localhost"])
async def test_negative_invalid_database_url(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    create = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create)
    generator = pool.__wrapped__()
    with pytest.raises(ValueError):
        await anext(generator)
    create.assert_not_awaited()


async def test_negative_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    create = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(KeyError, match="DATABASE_URL"):
        await anext(pool.__wrapped__())
    create.assert_not_awaited()


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql", "postgres"])
async def test_pool_normalizes_dsn_and_closes(monkeypatch, scheme):
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://localhost/aios_test_fixture")
    connection_pool = AsyncMock()
    create = AsyncMock(return_value=connection_pool)
    monkeypatch.setattr(asyncpg, "create_pool", create)
    generator = pool.__wrapped__()
    assert await anext(generator) is connection_pool
    expected_scheme = "postgresql" if scheme == "postgresql+asyncpg" else scheme
    create.assert_awaited_once_with(
        f"{expected_scheme}://localhost/aios_test_fixture", min_size=1, max_size=16
    )
    connection_pool.close.assert_not_awaited()
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    connection_pool.close.assert_awaited_once_with()


async def test_failure_injection_pool_creation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test_fixture")
    failure = OSError("injected connection failure")
    create = AsyncMock(side_effect=failure)
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(OSError) as caught:
        await anext(pool.__wrapped__())
    assert caught.value is failure
    assert create.await_count == 1


async def test_failure_injection_consumer_closes_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test_fixture")
    connection_pool = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=connection_pool))
    generator = pool.__wrapped__()
    await anext(generator)
    failure = RuntimeError("injected consumer failure")
    with pytest.raises(RuntimeError) as caught:
        await generator.athrow(failure)
    assert caught.value is failure
    connection_pool.close.assert_awaited_once_with()


async def test_failure_injection_pool_close(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test_fixture")
    connection_pool = AsyncMock()
    failure = OSError("injected close failure")
    connection_pool.close.side_effect = failure
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=connection_pool))
    generator = pool.__wrapped__()
    await anext(generator)
    with pytest.raises(OSError) as caught:
        await anext(generator)
    assert caught.value is failure
    connection_pool.close.assert_awaited_once_with()
