"""18.3 통합테스트 — 실제 dev DB 대상.

완료조건(SUSPENDED 사용자 로그인 거부)은 AuthService(11.2)와의 연동을
실제로 실증한다.
"""
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.auth_service import AuthError, AuthService
from src.services.user_admin_service import UserAdminError, UserAdminService
from tests.integration.conftest import create_test_user

JWT_SECRET = "test-secret-key-not-for-production"
STRONG_PASSWORD = "Str0ng!Passw0rd"
_ADMIN_ID = uuid.uuid4()


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


@pytest.fixture
def service(pool):
    return UserAdminService(pool)


async def test_list_users_filters_by_email_search(service, pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        email = await conn.fetchval("SELECT email FROM users WHERE user_id = $1", user_id)
    unique_fragment = email.split("@")[0]

    results = await service.list_users(email_search=unique_fragment)

    assert any(u.user_id == user_id for u in results)


async def test_change_status_to_suspended(service, pool):
    user_id = await create_test_user(pool)

    result = await service.change_status(user_id, "SUSPENDED", admin_user_id=_ADMIN_ID)

    assert result.status == "SUSPENDED"
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM users WHERE user_id = $1", user_id)
    assert status == "SUSPENDED"


async def test_change_status_rejects_deleted(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(UserAdminError):
        await service.change_status(user_id, "DELETED", admin_user_id=_ADMIN_ID)


async def test_change_status_rejects_pending_deletion(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(UserAdminError):
        await service.change_status(user_id, "PENDING_DELETION", admin_user_id=_ADMIN_ID)


async def test_change_status_rejects_nonexistent_user(service):
    with pytest.raises(UserAdminError):
        await service.change_status(uuid.uuid4(), "SUSPENDED", admin_user_id=_ADMIN_ID)


async def test_change_status_is_audit_logged_with_admin_as_actor(service, pool):
    """FD-7.2 감사기록 — actor_agent는 대상 사용자가 아니라 실제로
    변경을 실행한 운영자여야 한다."""
    import json

    user_id = await create_test_user(pool)

    await service.change_status(user_id, "SUSPENDED", admin_user_id=_ADMIN_ID)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT actor_agent, decision_data FROM audit_log "
            "WHERE user_id = $1 AND action_type = 'user.status_changed'",
            user_id,
        )
    assert row is not None
    assert row["actor_agent"] == str(_ADMIN_ID)
    decision_data = json.loads(row["decision_data"])
    assert decision_data["new_status"] == "SUSPENDED"


async def test_suspended_user_login_rejected(service, pool):
    auth = AuthService(pool, jwt_secret_key=JWT_SECRET)
    email = f"test-{uuid.uuid4().hex}@example.com"
    user = await auth.signup(email, STRONG_PASSWORD)

    await service.change_status(user.user_id, "SUSPENDED", admin_user_id=_ADMIN_ID)

    with pytest.raises(AuthError):
        await auth.authenticate(email, STRONG_PASSWORD)


async def test_reactivating_user_allows_login_again(service, pool):
    auth = AuthService(pool, jwt_secret_key=JWT_SECRET)
    email = f"test-{uuid.uuid4().hex}@example.com"
    user = await auth.signup(email, STRONG_PASSWORD)
    await service.change_status(user.user_id, "SUSPENDED", admin_user_id=_ADMIN_ID)

    await service.change_status(user.user_id, "ACTIVE", admin_user_id=_ADMIN_ID)
    logged_in = await auth.authenticate(email, STRONG_PASSWORD)

    assert logged_in.email == email
