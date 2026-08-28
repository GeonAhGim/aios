"""10.3 통합테스트 — 실제 dev DB 대상(approval_requests fallback 경로만
DB를 쓴다 — withdrawal_whitelist는 아직 없어 fetch_whitelist를 인메모리
스텁으로 주입한다)."""
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval import service as approval
from src.core.approval.panic_prompt import (
    CorroborationSignal,
    PanicPromptGenerator,
    WhitelistEntry,
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


_WHITELIST = {
    "cold_wallet": WhitelistEntry(
        id=1, exchange="bitget", destination_address="bc1qcoldwallet", label="본인 콜드월렛"
    ),
}


async def _fetch_whitelist(user_id, exchange):  # noqa: ARG001 — 테스트 스텁, user_id 무시
    return [entry for entry in _WHITELIST.values() if entry.exchange == exchange]


async def _empty_whitelist(user_id, exchange):  # noqa: ARG001
    return []


async def test_two_agreeing_sources_activate_fast_path(pool):
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
        ],
    )

    assert result.fast_path_activated is True
    assert result.fallback_approval_request_id is None
    assert [d.id for d in result.destinations] == [1]


async def test_single_source_falls_back_to_normal_approval(pool):
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[CorroborationSignal(source="exchange_status_page", risk_confirmed=True)],
    )

    assert result.fast_path_activated is False
    assert result.destinations == []
    assert result.fallback_approval_request_id is not None

    request = await approval.get_request(pool, result.fallback_approval_request_id)
    assert request.status == "PENDING"
    assert request.requested_action == "EMERGENCY_WITHDRAWAL_REVIEW"


async def test_contradicting_sources_fall_back_to_normal_approval(pool):
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=False),
        ],
    )

    assert result.fast_path_activated is False
    assert result.fallback_approval_request_id is not None


async def test_no_sources_fall_back_to_normal_approval(pool):
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool), exchange="bitget", corroboration=[]
    )

    assert result.fast_path_activated is False
    assert result.fallback_approval_request_id is not None


async def test_empty_whitelist_activates_fast_path_with_no_destinations(pool):
    generator = PanicPromptGenerator(pool, fetch_whitelist=_empty_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
        ],
    )

    assert result.fast_path_activated is True
    assert result.destinations == []
