"""PLT-23 auth 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 PostgreSQL URL을 검증한 뒤 asyncpg DSN으로 변환한다
(패턴은 `tests/integration/foundation/ledger/conftest.py`와 동일).
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock

import asyncpg
import pytest


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    if not url or not url.startswith(("postgresql+asyncpg://", "postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be an explicit PostgreSQL URL")
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url.removeprefix("postgresql+asyncpg://")
    return url


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    try:
        yield p
    finally:
        await p.close()


async def test_negative_missing_database_url_rejected_before_connection(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    create = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(KeyError, match="DATABASE_URL"):
        await anext(pool.__wrapped__())
    create.assert_not_called()


@pytest.mark.parametrize("url", ["", "sqlite:///test.db", "https://localhost/test"])
async def test_negative_invalid_database_url_rejected_before_connection(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)
    create = AsyncMock()
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(ValueError, match="explicit PostgreSQL URL"):
        await anext(pool.__wrapped__())
    create.assert_not_called()


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql", "postgres"])
def test_dsn_conversion_preserves_connection_parameters(monkeypatch, scheme):
    suffix = "://localhost/aios_test?application_name=postgresql+asyncpg://auth"
    monkeypatch.setenv("DATABASE_URL", scheme + suffix)
    expected_scheme = "postgresql" if scheme == "postgresql+asyncpg" else scheme
    assert _asyncpg_dsn() == expected_scheme + suffix


async def test_failure_injection_pool_creation_error_propagates(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test")
    failure = ConnectionError("injected connection failure")
    create = AsyncMock(side_effect=failure)
    monkeypatch.setattr(asyncpg, "create_pool", create)
    with pytest.raises(ConnectionError) as caught:
        await anext(pool.__wrapped__())
    assert caught.value is failure
    create.assert_awaited_once_with(
        "postgresql://localhost/aios_test", min_size=1, max_size=8,
    )


@pytest.mark.parametrize("exit_mode", ["normal", "close", "failure"])
async def test_pool_cleanup_including_failure_injection(monkeypatch, exit_mode):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test")
    connection_pool = AsyncMock()
    create = AsyncMock(return_value=connection_pool)
    monkeypatch.setattr(asyncpg, "create_pool", create)
    fixture = pool.__wrapped__()
    assert await anext(fixture) is connection_pool
    connection_pool.close.assert_not_awaited()
    if exit_mode == "normal":
        with pytest.raises(StopAsyncIteration):
            await anext(fixture)
    elif exit_mode == "close":
        await fixture.aclose()
    else:
        failure = RuntimeError("injected consumer failure")
        with pytest.raises(RuntimeError) as caught:
            await fixture.athrow(failure)
        assert caught.value is failure
    connection_pool.close.assert_awaited_once_with()


async def test_failure_injection_pool_close_error_propagates(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/aios_test")
    failure = RuntimeError("injected close failure")
    connection_pool = AsyncMock()
    connection_pool.close.side_effect = failure
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(return_value=connection_pool))
    fixture = pool.__wrapped__()
    assert await anext(fixture) is connection_pool
    with pytest.raises(RuntimeError) as caught:
        await anext(fixture)
    assert caught.value is failure
    connection_pool.close.assert_awaited_once_with()
