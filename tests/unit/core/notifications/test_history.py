"""Tests for src/core/notifications/history.py (FD-17.3)

Coverage targets:
  - list_notification_history(): filter by event_type / start / end, combinations
  - empty-history edge case (no rows -> empty list, not an error)
  - negative / boundary inputs (unknown user, start > end, non-matching event_type)
  - failure injection (pool.acquire raising propagates, not swallowed)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.core.notifications.history import (
    NotificationHistoryEntry,
    list_notification_history,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def _insert(pool, user_id, event_type, *, channel="EMAIL", status="SENT"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO notifications (user_id, event_type, channel, status) "
            "VALUES ($1, $2, $3, $4)",
            user_id,
            event_type,
            channel,
            status,
        )


class TestListNotificationHistoryFilters:
    async def test_no_filters_returns_all_for_user_desc_order(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")
        await _insert(pool, user_id, "marketplace.purchase.requested")

        history = await list_notification_history(pool, user_id)

        assert len(history) == 2
        assert all(isinstance(e, NotificationHistoryEntry) for e in history)
        # ORDER BY created_at DESC
        assert history[0].created_at >= history[1].created_at

    async def test_filters_by_event_type(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")
        await _insert(pool, user_id, "marketplace.purchase.requested")

        history = await list_notification_history(
            pool, user_id, event_type="approval.request.created"
        )

        assert len(history) == 1
        assert history[0].event_type == "approval.request.created"

    async def test_filters_by_start_and_end_range(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")

        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=5)
        end = now + timedelta(minutes=5)

        history = await list_notification_history(pool, user_id, start=start, end=end)

        assert len(history) == 1

    async def test_channel_and_status_are_populated(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created", channel="PUSH", status="FAILED")

        history = await list_notification_history(pool, user_id)

        assert history[0].channel == "PUSH"
        assert history[0].status == "FAILED"


class TestListNotificationHistoryNegativeAndBoundary:
    """Negative / boundary inputs — wrong or edge-case parameters."""

    async def test_empty_history_returns_empty_list_not_error(self, pool):
        """FD-17.3 edge case: no history for the period -> [] not an exception."""
        history = await list_notification_history(pool, uuid4())
        assert history == []

    async def test_unknown_user_id_returns_empty_list(self, pool):
        user_a = await create_test_user(pool)
        await _insert(pool, user_a, "approval.request.created")

        history = await list_notification_history(pool, uuid4())

        assert history == []

    async def test_non_matching_event_type_returns_empty_list(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")

        history = await list_notification_history(
            pool, user_id, event_type="nonexistent.event.type"
        )

        assert history == []

    async def test_start_after_end_returns_empty_list(self, pool):
        """Boundary: start > end is a contradictory range -> no rows, not an error."""
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")

        now = datetime.now(timezone.utc)
        history = await list_notification_history(
            pool, user_id, start=now + timedelta(minutes=5), end=now - timedelta(minutes=5)
        )

        assert history == []

    async def test_end_before_any_record_excludes_it(self, pool):
        user_id = await create_test_user(pool)
        await _insert(pool, user_id, "approval.request.created")

        far_past = datetime.now(timezone.utc) - timedelta(days=365)
        history = await list_notification_history(pool, user_id, end=far_past)

        assert history == []

    async def test_does_not_leak_other_users_history(self, pool):
        user_a, user_b = await create_test_user(pool), await create_test_user(pool)
        await _insert(pool, user_a, "approval.request.created")

        history = await list_notification_history(pool, user_b)

        assert history == []


class TestListNotificationHistoryFailureInjection:
    """Failure injection: dependency (pool) raising must propagate, fail-closed."""

    async def test_pool_acquire_failure_propagates(self, pool, monkeypatch):
        user_id = await create_test_user(pool)

        class FailingPool:
            def acquire(self):
                raise RuntimeError("simulated pool exhaustion")

        with pytest.raises(RuntimeError, match="simulated pool exhaustion"):
            await list_notification_history(FailingPool(), user_id)

    async def test_connection_fetch_failure_propagates(self, pool, monkeypatch):
        user_id = await create_test_user(pool)

        class FailingConn:
            async def fetch(self, *args, **kwargs):
                raise asyncpg.PostgresConnectionError("simulated connection drop")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        class FailingAcquirePool:
            def acquire(self):
                return FailingConn()

        with pytest.raises(asyncpg.PostgresConnectionError):
            await list_notification_history(FailingAcquirePool(), user_id)
