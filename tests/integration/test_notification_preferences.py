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


async def test_get_preferences_defaults_to_all_true_for_new_user(pool):
    prefs = await get_notification_preferences(pool, uuid4())
    assert prefs == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }


async def test_update_applies_allowed_field(pool):
    user_id = await create_test_user(pool)
    result = await update_notification_preferences(
        pool, user_id, {"marketplace_purchase_email": False}
    )
    assert result.applied["marketplace_purchase_email"] is False
    assert result.rejected_fields == []

    refetched = await get_notification_preferences(pool, user_id)
    assert refetched["marketplace_purchase_email"] is False


async def test_update_rejects_forced_field_but_applies_the_rest(pool):
    user_id = await create_test_user(pool)
    result = await update_notification_preferences(
        pool,
        user_id,
        {"marketplace_purchase_email": False, "human_approval_requested_email": False},
    )
    assert result.rejected_fields == ["human_approval_requested_email"]
    assert result.applied["marketplace_purchase_email"] is False


async def test_update_partial_upsert_preserves_other_fields(pool):
    user_id = await create_test_user(pool)
    await update_notification_preferences(pool, user_id, {"risk_mismatch_email": False})
    result = await update_notification_preferences(
        pool, user_id, {"marketplace_purchase_email": False}
    )
    assert result.applied["risk_mismatch_email"] is False  # 이전 변경 유지
    assert result.applied["marketplace_purchase_email"] is False


# --- negative tests (DoD: ≥3 건) ---


async def test_update_rejects_unknown_field_name(pool):
    """불 whitelist 밖의 필드명 — 명시적으로 거부되어야 함."""
    user_id = await create_test_user(pool)
    result = await update_notification_preferences(pool, user_id, {"unknown_field": True})
    assert result.rejected_fields == ["unknown_field"]
    # applied는 DB에서 읽은 전체 preferences (변경 없음 = 기본값 유지)
    assert result.applied == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }


async def test_update_all_unknown_fields_returns_empty_applied(pool):
    """전체 입력이 whitelist 밖일 때 applied는 DB 현재값, rejected는 전체."""
    user_id = await create_test_user(pool)
    result = await update_notification_preferences(
        pool,
        user_id,
        {
            "human_approval_requested_email": False,
            "some_other_channel": True,
        },
    )
    assert set(result.rejected_fields) == {
        "human_approval_requested_email",
        "some_other_channel",
    }
    # allowed가 비어 있으므로 DB에서 읽은 값은 기본값
    assert result.applied == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }


async def test_update_empty_changes_does_nothing(pool):
    """빈 변경 요청 — rejected도 applied 변경도 없음."""
    user_id = await create_test_user(pool)
    result = await update_notification_preferences(pool, user_id, {})
    assert result.rejected_fields == []
    # applied는 DB 현재값 (변경 전 기본값)
    assert result.applied == {
        "marketplace_purchase_email": True,
        "verification_result_email": True,
        "risk_mismatch_email": True,
    }


# --- failure injection test ---


async def test_update_handles_db_connection_error(pool, monkeypatch):
    """DB 연결 실패 시 예외가 상위 레이어로 전파되어야 함."""
    user_id = await create_test_user(pool)
    from unittest.mock import AsyncMock, patch

    import asyncpg

    async def failing_execute(*args, **kwargs):
        raise asyncpg.exceptions.ConnectionDoesNotExistError("connection does not exist")

    with patch.object(
        asyncpg.connection.Connection, "execute", new=AsyncMock(side_effect=failing_execute)
    ):
        with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
            await update_notification_preferences(
                pool, user_id, {"marketplace_purchase_email": False}
            )
