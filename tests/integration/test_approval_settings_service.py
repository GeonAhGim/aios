"""11.4 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.approval_settings_service import ApprovalSettingsError, ApprovalSettingsService
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
    return ApprovalSettingsService(pool)


async def test_get_defaults_to_solo_for_user_with_no_row(service, pool):
    user_id = await create_test_user(pool)

    settings = await service.get(user_id)

    assert settings.mode == "SOLO"
    assert settings.mandatory_wait_seconds == 60
    assert settings.second_approver_contact is None


async def test_update_to_dual_requires_second_approver_contact(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ApprovalSettingsError):
        await service.update(user_id, mode="DUAL")


async def test_update_to_dual_with_contact_succeeds(service, pool):
    user_id = await create_test_user(pool)

    settings = await service.update(
        user_id, mode="DUAL", second_approver_contact="backup@example.com"
    )

    assert settings.mode == "DUAL"
    assert settings.second_approver_contact == "backup@example.com"
    assert settings.mandatory_wait_seconds == 60


async def test_update_rejects_unknown_mode(service, pool):
    user_id = await create_test_user(pool)

    with pytest.raises(ApprovalSettingsError):
        await service.update(user_id, mode="TRIPLE")


async def test_update_upserts_existing_row(service, pool):
    user_id = await create_test_user(pool)
    await service.update(user_id, mode="DUAL", second_approver_contact="a@example.com")

    settings = await service.update(user_id, mode="SOLO")

    assert settings.mode == "SOLO"
    refetched = await service.get(user_id)
    assert refetched.mode == "SOLO"
