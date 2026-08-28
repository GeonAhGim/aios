"""11.7 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval.panic_prompt import CorroborationSignal, PanicPromptGenerator
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.services.withdrawal_whitelist_service import (
    WithdrawalWhitelistError,
    WithdrawalWhitelistService,
)
from tests.integration.conftest import create_test_user

ENCRYPTION_KEY = "11" * 32


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def circuit_breaker(pool):
    return CircuitBreakerService(pool, load_risk_policy().circuit_breaker)


@pytest.fixture
def whitelist(pool, circuit_breaker):
    return WithdrawalWhitelistService(pool, circuit_breaker, encryption_key=ENCRYPTION_KEY)


async def test_register_and_list_round_trip(whitelist, pool):
    user_id = await create_test_user(pool)

    entry = await whitelist.register(
        user_id, exchange="bitget", destination_address="bc1qcoldwallet", label="콜드월렛"
    )
    assert entry.destination_address == "bc1qcoldwallet"

    entries = await whitelist.list_for_user(user_id)
    assert len(entries) == 1
    assert entries[0].destination_address == "bc1qcoldwallet"
    assert entries[0].label == "콜드월렛"


async def test_destination_address_stored_encrypted_not_plaintext(whitelist, pool):
    user_id = await create_test_user(pool)
    await whitelist.register(user_id, exchange="bitget", destination_address="bc1qcoldwallet")

    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT destination_address FROM withdrawal_whitelist WHERE user_id = $1", user_id
        )
    assert raw != "bc1qcoldwallet"


async def test_registration_blocked_during_crisis(whitelist, pool, circuit_breaker):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'restricted' WHERE id = 1"
        )

    with pytest.raises(WithdrawalWhitelistError):
        await whitelist.register(user_id, exchange="bitget", destination_address="bc1qattacker")

    entries = await whitelist.list_for_user(user_id)
    assert entries == []


async def test_registration_allowed_again_after_crisis_clears(whitelist, pool):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'warning' WHERE id = 1"
        )

    entry = await whitelist.register(
        user_id, exchange="bitget", destination_address="bc1qcoldwallet"
    )
    assert entry.exchange == "bitget"


async def test_fetch_for_panic_prompt_connects_to_panic_prompt_generator(whitelist, pool):
    user_id = await create_test_user(pool)
    await whitelist.register(user_id, exchange="bitget", destination_address="bc1qcoldwallet")
    await whitelist.register(user_id, exchange="kis", destination_address="110-1234-5678")

    generator = PanicPromptGenerator(pool, fetch_whitelist=whitelist.fetch_for_panic_prompt)
    result = await generator.generate(
        user_id=user_id,
        exchange="bitget",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
        ],
    )

    assert result.fast_path_activated is True
    assert [d.destination_address for d in result.destinations] == ["bc1qcoldwallet"]
