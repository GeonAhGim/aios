"""FD-13.11 통합테스트 — 실제 dev DB 대상 (WalletService)."""
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.wallet_service import WalletService, WalletTopupError
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
    return WalletService(pool)


async def test_get_balance_defaults_to_zero_for_new_user(service, pool):
    user = await create_test_user(pool)

    balance = await service.get_balance(user)

    assert balance.balance == Decimal("0")


async def test_request_topup_rejects_non_positive_amount(service, pool):
    user = await create_test_user(pool)

    with pytest.raises(WalletTopupError):
        await service.request_topup(user, Decimal("0"))


async def test_confirm_topup_credits_balance(service, pool):
    user = await create_test_user(pool)
    admin = await create_test_user(pool)
    topup = await service.request_topup(user, Decimal("30000"))

    result = await service.confirm_topup(topup.id, admin, idempotency_key="key-1")

    assert result.status == "CONFIRMED"
    assert result.balance_after == Decimal("30000")
    balance = await service.get_balance(user)
    assert balance.balance == Decimal("30000")


async def test_confirm_topup_excludes_from_pending_list(service, pool):
    user = await create_test_user(pool)
    admin = await create_test_user(pool)
    topup = await service.request_topup(user, Decimal("1000"))

    page_before = await service.list_pending_topups(page_size=1000)
    assert any(item.id == topup.id for item in page_before.items)

    await service.confirm_topup(topup.id, admin, idempotency_key="key-1")

    page_after = await service.list_pending_topups(page_size=1000)
    assert all(item.id != topup.id for item in page_after.items)


async def test_confirm_topup_is_idempotent_and_does_not_double_credit(service, pool):
    user = await create_test_user(pool)
    admin = await create_test_user(pool)
    topup = await service.request_topup(user, Decimal("5000"))

    await service.confirm_topup(topup.id, admin, idempotency_key="key-1")
    await service.confirm_topup(topup.id, admin, idempotency_key="key-2")
    await service.confirm_topup(topup.id, admin, idempotency_key="key-3")

    balance = await service.get_balance(user)
    assert balance.balance == Decimal("5000")


async def test_confirm_topup_rejects_nonexistent_request(service, pool):
    admin = await create_test_user(pool)

    with pytest.raises(WalletTopupError):
        await service.confirm_topup(999999999, admin, idempotency_key="key-1")
