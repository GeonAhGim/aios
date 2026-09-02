"""14.3 통합테스트 — 실제 dev DB 대상."""
import asyncio
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


async def test_list_strategies_returns_only_owned_strategies(service, pool):
    owner = await create_test_user(pool)
    other = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    other_strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )
    await service.save_strategy(
        other,
        other_strategy_id,
        "1.0.0",
        target_asset="ETH/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )

    summaries = await service.list_strategies(owner)

    assert any(s.strategy_id == strategy_id for s in summaries)
    assert all(s.strategy_id != other_strategy_id for s in summaries)


async def test_list_strategies_empty_for_new_user(service, pool):
    owner = await create_test_user(pool)

    summaries = await service.list_strategies(owner)

    assert summaries == []


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


async def test_concurrent_transitions_only_one_succeeds(service, pool, monkeypatch):
    """레드팀 감사 #17 — transition_lifecycle()이 방금 읽은 lifecycle_status를
    UPDATE 조건으로 다시 걸지 않으면, 거의 동시에 들어온 두 전이 요청이
    서로의 아직 커밋 안 된 변경을 못 본 채 둘 다 통과해버릴 수 있다
    (04/05/08/09/16번과 같은 "읽고 나서 별도로 조건 없이 쓰기" 근본원인).
    같은 GENERATED 상태에서 동시에 BACKTESTING 전이를 두 번 시도하면
    정확히 하나만 성공해야 한다.

    asyncio.gather만으로는 두 transition_lifecycle() 호출의 사전조회가
    실제로 동시에 겹친다는 보장이 없다 — #04/#05와 같은 원칙으로 barrier를
    걸어 원래 레이스 조건을 결정적으로 재현한다."""
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

    arrived = 0
    released = asyncio.Event()
    original_fetchrow = asyncpg.pool.PoolConnectionProxy.fetchrow

    async def _synced_fetchrow(self, query, *args, **kwargs):
        nonlocal arrived
        result = await original_fetchrow(self, query, *args, **kwargs)
        if "SELECT s.lifecycle_status" in query:
            arrived += 1
            if arrived >= 2:
                released.set()
            else:
                await released.wait()
        return result

    monkeypatch.setattr(asyncpg.pool.PoolConnectionProxy, "fetchrow", _synced_fetchrow)

    results = await asyncio.gather(
        service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING"),
        service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, StrategyLifecycleError)]
    assert len(successes) == 1
    assert len(failures) == 1


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
