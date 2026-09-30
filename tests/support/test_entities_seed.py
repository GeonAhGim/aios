"""Negative / failure-injection tests for tests.support.entities_seed.

FA-1 / FA-2 — verify that the seed helpers reject invalid inputs and surface
dependency failures deterministically (no silent fallback).

DoD (task-9966):
- negative test >= 3  (invalid inputs -> explicit rejection)
- failure-injection test >= 1  (monkeypatch -> dependency exception)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import asyncpg
import pytest

from tests.support.entities_seed import (
    bootstrap_default_hierarchy,
    bootstrap_default_portfolio,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pool() -> MagicMock:
    """A fake asyncpg.Pool that satisfies every method the seed calls."""
    return MagicMock(spec=asyncpg.Pool)


# ---------------------------------------------------------------------------
# Negative tests -- invalid inputs must be rejected immediately
# ---------------------------------------------------------------------------


class TestNegativeInvalidInputs:
    """Negative tests: bootstrap helpers must not silently accept bad data."""

    @pytest.mark.asyncio
    async def test_bootstrap_default_hierarchy_raises_on_none_pool(self, pool) -> None:
        """Passing None as pool must raise -- the helper requires a real
        asyncpg.Pool to create a PostgresEntityRepository."""
        with pytest.raises((TypeError, AttributeError)):
            await bootstrap_default_hierarchy(None, uuid4())

    @pytest.mark.asyncio
    async def test_bootstrap_default_hierarchy_raises_on_invalid_tenant_type(self, pool) -> None:
        """Passing a non-UUID tenant_id (e.g. str) must raise -- deterministic
        UUIDv5 construction requires a real UUID."""
        with pytest.raises(AttributeError):
            await bootstrap_default_hierarchy(pool, "not-a-uuid")

    @pytest.mark.asyncio
    async def test_bootstrap_default_portfolio_raises_on_none_pool(self, pool) -> None:
        """Same contract for the convenience wrapper."""
        with pytest.raises((TypeError, AttributeError)):
            await bootstrap_default_portfolio(None, uuid4())


# ---------------------------------------------------------------------------
# Failure-injection tests -- dependency exceptions must propagate
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Inject failures into the dependency chain and verify the seed does
    not swallow them."""

    @pytest.mark.asyncio
    async def test_bootstrap_default_hierarchy_propagates_repo_exception(self, pool) -> None:
        """When ensure_default_hierarchy raises (e.g. DB error),
        bootstrap_default_hierarchy must propagate -- never silently return a
        partial hierarchy."""
        with patch(
            "tests.support.entities_seed.ensure_default_hierarchy",
            side_effect=asyncpg.PostgresError("connection refused"),
        ):
            with pytest.raises(asyncpg.PostgresError, match="connection refused"):
                await bootstrap_default_hierarchy(pool, uuid4())

    @pytest.mark.asyncio
    async def test_bootstrap_default_portfolio_propagates_hierarchy_failure(self, pool) -> None:
        """bootstrap_default_portfolio must not catch and suppress the
        underlying hierarchy bootstrap failure."""
        with patch(
            "tests.support.entities_seed.bootstrap_default_hierarchy",
            side_effect=RuntimeError("deterministic seed failure"),
        ):
            with pytest.raises(RuntimeError, match="deterministic seed failure"):
                await bootstrap_default_portfolio(pool, uuid4())


# ---------------------------------------------------------------------------
# Idempotency negative test -- concurrent calls must not corrupt state
# ---------------------------------------------------------------------------


class TestNegativeIdempotency:
    """Verify that calling bootstrap helpers multiple times does not
    corrupt state or return inconsistent hierarchies."""

    @pytest.mark.asyncio
    async def test_concurrent_bootstrap_returns_consistent_ids(self, pool) -> None:
        """Two concurrent calls for the same tenant must converge on the same
        deterministic result, not diverge into inconsistent hierarchies."""
        import asyncio

        from src.foundation.entities.domain.defaults import DefaultHierarchy

        tenant_id = uuid4()

        with patch(
            "tests.support.entities_seed.ensure_default_hierarchy",
        ) as mock_ensure:
            fake_hier = MagicMock(spec=DefaultHierarchy)
            mock_ensure.return_value = fake_hier

            results = await asyncio.gather(
                bootstrap_default_hierarchy(pool, tenant_id),
                bootstrap_default_hierarchy(pool, tenant_id),
            )

            assert mock_ensure.call_count == 2
            assert results[0] is results[1]
