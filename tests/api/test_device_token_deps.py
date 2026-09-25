"""Tests for src/api/device_token_deps.py dependency injection.

Covers:
- Happy-path: get_device_token_service returns instance with pool
- Negative: pool is None, pool invalid type
- Failure injection: constructor raises
- Boundary: multiple calls create independent instances
"""

from unittest.mock import MagicMock, patch

import pytest

from src.api.device_token_deps import (
    get_device_token_service,
)

# ── Happy-path tests ─────────────────────────────────────────────────────────


def test_get_device_token_service_returns_instance() -> None:
    """get_device_token_service returns a DeviceTokenService instance."""
    result = get_device_token_service()
    from src.services.device_token_service import DeviceTokenService

    assert isinstance(result, DeviceTokenService)


def test_get_device_token_service_with_pool() -> None:
    """get_device_token_service creates DeviceTokenService with the pool."""
    mock_pool = MagicMock()
    result = get_device_token_service(pool=mock_pool)
    from src.services.device_token_service import DeviceTokenService

    assert isinstance(result, DeviceTokenService)
    assert result._pool is mock_pool


# ── Negative tests ────────────────────────────────────────────────────────────


def test_get_device_token_service_pool_none() -> None:
    """Pool is None → DeviceTokenService stores None (validated later at use time)."""
    result = get_device_token_service(pool=None)
    assert result._pool is None


def test_get_device_token_service_pool_invalid_type() -> None:
    """Pool is not an asyncpg pool (e.g. a plain dict) → DeviceTokenService accepts it."""
    bad_pool = {"not": "a pool"}
    result = get_device_token_service(pool=bad_pool)
    assert result._pool is bad_pool


def test_get_device_token_service_pool_empty_string() -> None:
    """Pool is an empty string → DeviceTokenService stores it (no validation at init)."""
    result = get_device_token_service(pool="")
    assert result._pool == ""


# ── Failure injection tests ───────────────────────────────────────────────────


def test_get_device_token_service_constructor_raises() -> None:
    """DeviceTokenService.__init__ raises → exception propagates to caller."""
    with patch(
        "src.api.device_token_deps.DeviceTokenService",
        side_effect=RuntimeError("DB connection lost"),
    ) as mock_cls:
        mock_pool = MagicMock()
        with pytest.raises(RuntimeError, match="DB connection lost"):
            get_device_token_service(pool=mock_pool)
        mock_cls.assert_called_once_with(mock_pool)


def test_get_device_token_service_constructor_raises_no_pool() -> None:
    """DeviceTokenService.__init__ raises with no pool arg → exception propagates."""
    with patch(
        "src.api.device_token_deps.DeviceTokenService",
        side_effect=ConnectionError("cannot reach DB"),
    ) as mock_cls:
        with pytest.raises(ConnectionError, match="cannot reach DB"):
            get_device_token_service()
        mock_cls.assert_called_once()


# ── Boundary tests ────────────────────────────────────────────────────────────


def test_multiple_calls_create_independent_instances() -> None:
    """Each call to get_device_token_service creates a new instance."""
    mock_pool = MagicMock()
    a = get_device_token_service(pool=mock_pool)
    b = get_device_token_service(pool=mock_pool)
    assert a is not b
    assert a._pool is b._pool is mock_pool
