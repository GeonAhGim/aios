"""21.1 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.device_token_service import DeviceTokenError, DeviceTokenService
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


@pytest.fixture
def service(pool):
    return DeviceTokenService(pool)


async def test_register_creates_active_token(service, pool):
    user_id = await create_test_user(pool)

    result = await service.register(user_id, "token-abc", "iOS")

    assert result.is_active is True
    tokens = await service.list_active_tokens(user_id)
    assert "token-abc" in tokens


async def test_register_rejects_unknown_platform(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(DeviceTokenError):
        await service.register(user_id, "token-abc", "WindowsPhone")


async def test_deactivate_removes_from_active_list(service, pool):
    user_id = await create_test_user(pool)
    registered = await service.register(user_id, "token-abc", "Android")

    await service.deactivate(registered.device_id, user_id)

    tokens = await service.list_active_tokens(user_id)
    assert "token-abc" not in tokens


async def test_deactivate_rejects_other_users_device(service, pool):
    owner_id = await create_test_user(pool)
    stranger_id = await create_test_user(pool)
    registered = await service.register(owner_id, "token-abc", "Android")

    with pytest.raises(DeviceTokenError):
        await service.deactivate(registered.device_id, stranger_id)

    tokens = await service.list_active_tokens(owner_id)
    assert "token-abc" in tokens


async def test_reregistering_same_token_after_deactivation_succeeds(service, pool):
    """v1.3 재점검 라운드 정정 — 해지 후 같은 토큰으로 재등록이 막히지 않는다."""
    user_id = await create_test_user(pool)
    first = await service.register(user_id, "token-abc", "iOS")
    await service.deactivate(first.device_id, user_id)

    second = await service.register(user_id, "token-abc", "iOS")

    assert second.is_active is True
    tokens = await service.list_active_tokens(user_id)
    assert "token-abc" in tokens


async def test_registering_same_active_token_twice_is_idempotent(service, pool):
    user_id = await create_test_user(pool)
    first = await service.register(user_id, "token-abc", "iOS")

    second = await service.register(user_id, "token-abc", "iOS")

    assert second.device_id == first.device_id


async def test_deactivate_rejects_unknown_device(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(DeviceTokenError):
        await service.deactivate(999999999, user_id)


async def test_list_active_tokens_empty_for_new_user(service, pool):
    user_id = await create_test_user(pool)

    tokens = await service.list_active_tokens(user_id)

    assert tokens == []
