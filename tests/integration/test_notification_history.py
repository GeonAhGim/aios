"""17.3 통합테스트 — 실제 dev DB 대상."""
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
