"""17.3 통합테스트 — 실제 dev DB 대상."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.notifications.history import list_notification_history
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _insert(pool, user_id, event_type, channel="EMAIL", status="SENT"):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO notifications (user_id, event_type, channel, status) "
            "VALUES ($1, $2, $3, $4)",
            user_id,
            event_type,
            channel,
            status,
        )


async def test_empty_history_returns_empty_list_not_error(pool):
    history = await list_notification_history(pool, uuid4())
    assert history == []


async def test_history_filters_by_event_type(pool):
    user_id = await create_test_user(pool)
    await _insert(pool, user_id, "approval.request.created")
    await _insert(pool, user_id, "marketplace.purchase.requested")

    history = await list_notification_history(pool, user_id, event_type="approval.request.created")

    assert len(history) == 1
    assert history[0].event_type == "approval.request.created"


async def test_history_does_not_leak_other_users(pool):
    user_a, user_b = await create_test_user(pool), uuid4()
    await _insert(pool, user_a, "approval.request.created")

    history = await list_notification_history(pool, user_b)

    assert history == []


async def test_history_unknown_event_type_returns_empty(pool):
    # negative: a filter that matches no row must not fall back to unfiltered rows
    user_id = await create_test_user(pool)
    await _insert(pool, user_id, "approval.request.created")

    history = await list_notification_history(pool, user_id, event_type="no.such.event")

    assert history == []


async def test_history_inverted_window_returns_empty(pool):
    # negative: start after end is an empty window, never the whole history
    user_id = await create_test_user(pool)
    await _insert(pool, user_id, "approval.request.created")
    now = datetime.now(UTC)

    history = await list_notification_history(
        pool, user_id, start=now + timedelta(hours=1), end=now - timedelta(hours=1)
    )

    assert history == []


async def test_history_other_user_cannot_read_via_event_filter(pool):
    # negative: the user_id predicate still applies when an event_type filter is set
    user_a, user_b = await create_test_user(pool), await create_test_user(pool)
    await _insert(pool, user_a, "approval.request.created")

    history = await list_notification_history(pool, user_b, event_type="approval.request.created")

    assert history == []


async def test_history_db_failure_propagates_not_empty(monkeypatch):
    # failure injection: a dead connection must surface as an error, never as an
    # empty history, otherwise "no notification delivered" becomes indistinguishable
    # from "the lookup failed".
    class _BrokenPool:
        def acquire(self):
            raise asyncpg.InterfaceError("injected connection failure")

    with pytest.raises(asyncpg.InterfaceError, match="injected connection failure"):
        await list_notification_history(_BrokenPool(), uuid4())


@pytest.mark.perf
async def test_history_lookup_latency_within_budget(pool, perf_budget) -> None:
    # D2 performance assertion: best-of-5 wall time against a 50ms budget (same
    # budget as the CM-16 decision lookup), measured through perf_budget.
    user_id = await create_test_user(pool)
    for _ in range(20):
        await _insert(pool, user_id, "approval.request.created")

    async def _lookup() -> None:
        await list_notification_history(pool, user_id, event_type="approval.request.created")

    result = await perf_budget.best_of_async(_lookup, n=5)
    assert result.wall_ms < 50.0, f"history lookup={result.wall_ms:.2f}ms, budget 50ms exceeded"
