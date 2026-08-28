"""14.3 통합테스트 — 실제 dev DB 대상."""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.strategy_builder_service import (
    LIFECYCLE_ORDER,
    StrategyBuilderService,
    StrategyLifecycleError,
    assert_executable,
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


@pytest.fixture
def service(pool):
    return StrategyBuilderService(pool)


async def test_save_strategy_starts_at_generated(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    saved = await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )

    assert saved.lifecycle_status == "GENERATED"


async def test_save_strategy_rejects_duplicate_id_version(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={},
        )


async def test_transition_to_next_stage_succeeds(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    result = await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    assert result.lifecycle_status == "BACKTESTING"


async def test_cannot_skip_lifecycle_stages(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.transition_lifecycle(strategy_id, "1.0.0", "RISK_REVIEW")


async def test_full_lifecycle_walk_reaches_approved(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    result = None
    for stage in LIFECYCLE_ORDER[1:7]:  # BACKTESTING ... APPROVED
        result = await service.transition_lifecycle(strategy_id, "1.0.0", stage)

    assert result.lifecycle_status == "APPROVED"


async def test_can_fail_from_any_non_terminal_stage(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    result = await service.transition_lifecycle(strategy_id, "1.0.0", "FAILED")

    assert result.lifecycle_status == "FAILED"


async def test_cannot_transition_out_of_terminal_state(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await service.transition_lifecycle(strategy_id, "1.0.0", "FAILED")

    with pytest.raises(StrategyLifecycleError):
        await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")


def test_assert_executable_blocks_freshly_generated_strategy():
    with pytest.raises(StrategyLifecycleError):
        assert_executable("GENERATED")


def test_assert_executable_allows_approved_strategy():
    assert_executable("APPROVED")  # 예외가 발생하지 않으면 성공
