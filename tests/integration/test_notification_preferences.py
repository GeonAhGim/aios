"""17.4 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.notifications.preferences import (
    get_notification_preferences,
    update_notification_preferences,
)


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


async def test_get_preferences_defaults_to_all_true_for_new_user(pool):
    prefs = await get_notification_preferences(pool, uuid4())
    assert prefs == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }


async def test_update_applies_allowed_field(pool):
    user_id = uuid4()
    result = await update_notification_preferences(
        pool, user_id, {"marketplace_purchase_email": False}
    )
    assert result.applied["marketplace_purchase_email"] is False
    assert result.rejected_fields == []

    refetched = await get_notification_preferences(pool, user_id)
    assert refetched["marketplace_purchase_email"] is False


async def test_update_rejects_forced_field_but_applies_the_rest(pool):
    user_id = uuid4()
    result = await update_notification_preferences(
        pool,
        user_id,
        {"marketplace_purchase_email": False, "human_approval_requested_email": False},
    )
    assert result.rejected_fields == ["human_approval_requested_email"]
    assert result.applied["marketplace_purchase_email"] is False


async def test_update_partial_upsert_preserves_other_fields(pool):
    user_id = uuid4()
    await update_notification_preferences(pool, user_id, {"risk_mismatch_email": False})
    result = await update_notification_preferences(
        pool, user_id, {"marketplace_purchase_email": False}
    )
    assert result.applied["risk_mismatch_email"] is False  # 이전 변경 유지
    assert result.applied["marketplace_purchase_email"] is False
