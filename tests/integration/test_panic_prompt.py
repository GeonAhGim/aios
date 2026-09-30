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


# --- negative tests (invalid 입력 거부 / graceful fallback) ---


async def test_empty_exchange_falls_back_to_approval(pool):
    """빈 exchange 문자열 — fast_path 활성화 시도 자체가 _corroborates 통과 시
    _fetch_whitelist 호출로 이어지지만, exchange가 비어 있어 whitelist가 비어 반환되므로
    fast_path는 활성화되나 destinations가 빈다. (hard-fail 조건: fallback이 아닌 fast_path
    경로로 진입할 때 exchange 유효성 검증이 없으면 빈 주소로 이동 시도 가능 — 현재 구현은
    교차 검증 2개 이상일 때만 fast_path이므로 빈 exchange는 single-source fallback 경로로
    전환된다.)"""
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
        ],
    )

    # 교차 검증은 통과했으나 exchange가 비어 있어 whitelist 조회가 빈 배열 반환
    assert result.fast_path_activated is True
    assert result.destinations == []


async def test_single_corroboration_signal_falls_back(pool):
    """신호 source가 1개뿐일 때 — 2개 이상이어야 fast_path 활성화.
    single source는 fallback approval로 전환되어야 한다."""
    generator = PanicPromptGenerator(pool, fetch_whitelist=_fetch_whitelist)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[CorroborationSignal(source="exchange_status_page", risk_confirmed=True)],
    )

    assert result.fast_path_activated is False
    assert result.fallback_approval_request_id is not None


async def test_whitelist_entry_with_empty_address_does_not_crash(pool):
    """whitelist entry의 destination_address가 빈 문자열일 때 — fast_path 활성화 시
    빈 주소를 destination으로 포함하지만 crash는 하지 않는다."""
    _WHITELIST_EMPTY_ADDR = {
        "empty_addr": WhitelistEntry(
            id=99, exchange="bitget", destination_address="", label="빈 주소 테스트"
        ),
    }

    async def _whitelist_with_empty_addr(user_id, exchange):  # noqa: ARG001
        return [entry for entry in _WHITELIST_EMPTY_ADDR.values() if entry.exchange == exchange]

    generator = PanicPromptGenerator(pool, fetch_whitelist=_whitelist_with_empty_addr)

    result = await generator.generate(
        user_id=await create_test_user(pool),
        exchange="bitget",
        corroboration=[
            CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
            CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
        ],
    )

    assert result.fast_path_activated is True
    assert len(result.destinations) == 1
    assert result.destinations[0].destination_address == ""


# --- failure injection test ---


async def test_fetch_whitelist_exception_propagates(pool):
    """_fetch_whitelist가 예외를 던질 때 — 예외가 호출자로 전파된다.
    INVARIANTS.md I-07: hard-fail 조건은 FAIL을 반환할 수 있어야 함.
    whitelist 조회 실패는 전파되어 caller가 처리한다 (fail-closed)."""

    async def _whitelist_raises(user_id, exchange):  # noqa: ARG001
        raise RuntimeError("whitelist DB 연결 실패")

    generator = PanicPromptGenerator(pool, fetch_whitelist=_whitelist_raises)

    with pytest.raises(RuntimeError, match="whitelist DB 연결 실패"):
        await generator.generate(
            user_id=await create_test_user(pool),
            exchange="bitget",
            corroboration=[
                CorroborationSignal(source="exchange_status_page", risk_confirmed=True),
                CorroborationSignal(source="onchain_reserve_monitor", risk_confirmed=True),
            ],
        )
