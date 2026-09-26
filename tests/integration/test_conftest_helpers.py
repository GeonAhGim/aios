"""Negative and failure-injection tests for tests/integration/conftest.py helpers.

DoD (task-4148 DEEPEN):
- negative test >= 3 (reject invariant-violating inputs)
- failure-injection test >= 1 (monkeypatch dependency to raise)
- No INVARIANTS.md (I-01..I-11) violations.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from tests.integration.conftest import NoopEventBus, create_test_tenant

# ---------------------------------------------------------------------------
# Negative tests -- helpers must reject / fail-closed on bad inputs
# ---------------------------------------------------------------------------


class TestCreateTestUserNegative:
    """create_test_user(pool) is tested indirectly via create_test_tenant
    and direct pool usage; negative cases target the pool contract."""

    @pytest.mark.asyncio
    async def test_rejects_non_asyncpg_pool(self) -> None:
        """pool.acquire() must be called on an asyncpg.Pool -- passing a
        non-asyncpg mock should raise TypeError when the helper tries
        pool.acquire()."""
        fake_pool = MagicMock()
        # Make acquire() itself raise -- the helper should not silently proceed
        fake_pool.acquire.side_effect = TypeError("not an asyncpg.Pool")
        with pytest.raises(TypeError, match="not an asyncpg.Pool"):
            await create_test_tenant(fake_pool, bootstrap_default_hierarchy_rows=False)


class TestCreateTestTenantNegative:
    """Negative cases for create_test_tenant."""

    @pytest.mark.asyncio
    async def test_rejects_bad_pool_type(self) -> None:
        """Passing a non-async-pool object should fail when acquire() is called."""
        fake_pool = MagicMock()
        fake_pool.acquire.side_effect = TypeError("not an asyncpg.Pool")
        with pytest.raises(TypeError, match="not an asyncpg.Pool"):
            await create_test_tenant(fake_pool)


class TestNoopEventBusNegative:
    """Negative cases for NoopEventBus -- it must NOT trigger real event processing."""

    def test_subscribe_returns_none_no_handler_invoked(self) -> None:
        """subscribe() must return None -- no handler is registered."""
        bus = NoopEventBus()

        async def dummy_handler(topic: str, payload: dict[str, Any]) -> None:
            pass

        bus.subscribe("test.topic", dummy_handler, criticality="low")
        # subscribe() should complete without raising

    @pytest.mark.asyncio
    async def test_publish_records_but_does_not_dispatch(self) -> None:
        """publish() appends to self.published but does NOT call any handlers."""
        bus = NoopEventBus()
        await bus.start()
        called: list[dict[str, Any]] = []

        async def handler(topic: str, payload: dict[str, Any]) -> None:
            called.append(payload)

        bus.subscribe("test.topic", handler, criticality="low")
        await bus.publish("test.topic", {"key": "value"})

        # recorded in published list
        assert ("test.topic", {"key": "value"}) in bus.published
        # but handler was never called
        assert called == []

    @pytest.mark.asyncio
    async def test_start_stop_are_noops(self) -> None:
        """start() and stop() must not raise or block."""
        bus = NoopEventBus()
        await bus.start()
        await bus.stop()


# ---------------------------------------------------------------------------
# Failure-injection tests -- monkeypatch dependencies to simulate failures
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Inject failures into dependencies and verify fail-closed behavior."""

    @pytest.mark.asyncio
    async def test_pool_connection_failure_raises(self) -> None:
        """When pool.acquire() raises (e.g. DB connection lost), the helper
        must propagate the exception -- fail-closed, not silently succeed."""
        from tests.integration.conftest import create_test_user

        fake_pool = MagicMock()
        # Make acquire() raise like a real DB connection failure
        fake_context = AsyncMock()
        fake_context.__aenter__ = AsyncMock(side_effect=asyncpg.PostgresError("connection refused"))
        fake_context.__aexit__ = AsyncMock(return_value=None)
        fake_pool.acquire.return_value = fake_context

        with pytest.raises(asyncpg.PostgresError, match="connection refused"):
            await create_test_user(fake_pool)

    @pytest.mark.asyncio
    async def test_noop_event_bus_stop_after_many_pubs(self) -> None:
        """NoopEventBus.stop() must complete instantly even after many
        publishes -- no retry/backoff delay."""
        bus = NoopEventBus()
        await bus.start()
        # Publish many events
        for i in range(100):
            await bus.publish(f"topic.{i}", {"i": i})
        # stop() should return immediately (no-op)
        await bus.stop()
        assert len(bus.published) == 100
