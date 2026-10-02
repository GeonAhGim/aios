"""Integration tests — StrategyBuilderService lifecycle & condition validation."""

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


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# save_strategy — happy path & condition validation
# ---------------------------------------------------------------------------


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


async def test_save_strategy_accepts_well_formed_condition(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    saved = await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={
            "states": ["IDLE", "HOLDING"],
            "transitions": [
                {
                    "from_state": "IDLE",
                    "to_state": "HOLDING",
                    "condition": "RSI_timeperiod14 < 30",
                }
            ],
        },
    )

    assert saved.lifecycle_status == "GENERATED"


async def test_save_strategy_rejects_syntax_error_in_condition(service, pool):
    """L16 DoD — 문법 오류 fsm_definition 저장 400. `RSI_timeperiod14`와 임계값
    사이에 비교 연산자가 없어 `_ATOMIC_RE`가 매칭되지 않는 절이다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    with pytest.raises(StrategyLifecycleError):
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={
                "states": ["IDLE", "HOLDING"],
                "transitions": [
                    {
                        "from_state": "IDLE",
                        "to_state": "HOLDING",
                        "condition": "RSI_timeperiod14 30",
                    }
                ],
            },
        )


async def test_save_strategy_rejects_syntax_error_in_and_combined_condition(service, pool):
    """조합(AND)의 두 번째 절만 문법 오류인 경우에도 저장을 거부해야 한다 —
    첫 절만 검사하고 통과시키는 회귀를 막는다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    with pytest.raises(StrategyLifecycleError):
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={
                "states": ["IDLE", "HOLDING"],
                "transitions": [
                    {
                        "from_state": "IDLE",
                        "to_state": "HOLDING",
                        "condition": "RSI_timeperiod14 < 30 AND close BROKEN 90",
                    }
                ],
            },
        )


async def test_save_strategy_accepts_raw_market_column_condition(service, pool):
    """ConditionCompiler는 손절 조건에 지표가 아닌 raw market-data 컬럼(`close`
    등)도 그대로 쓴다(`condition_compiler.py`) — 이 조건도 `_ATOMIC_RE` 문법만
    지키면 저장은 통과해야 한다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    saved = await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={
            "states": ["HOLDING", "STOP_LOSS"],
            "transitions": [
                {
                    "from_state": "HOLDING",
                    "to_state": "STOP_LOSS",
                    "condition": "close < 90",
                }
            ],
        },
    )

    assert saved.lifecycle_status == "GENERATED"


async def test_save_strategy_accepts_order_filled_reserved_literal(service, pool):
    """`ORDER_FILLED`(condition_compiler.ORDER_FILLED)는 사용자 조건식이 아니라
    주문 체결 시스템 이벤트를 나타내는 예약 리터럴이라 `_ATOMIC_RE` 문법
    대상이 아니다 — 저장이 막히지 않아야 한다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    saved = await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={
            "states": ["IDLE", "BUY_ORDER_PENDING"],
            "transitions": [
                {
                    "from_state": "IDLE",
                    "to_state": "BUY_ORDER_PENDING",
                    "condition": "ORDER_FILLED",
                }
            ],
        },
    )

    assert saved.lifecycle_status == "GENERATED"


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


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
    """L16 DoD — 병렬 전이: 동시에 같은 stage 전이하면 하나만 성공해야 한다.
    asyncpg.fetchrow를 래핑해 두 호출이 교차되도록 유도한다."""
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


# ---------------------------------------------------------------------------
# assert_executable (pure-domain helpers — no DB)
# ---------------------------------------------------------------------------


def test_assert_executable_blocks_freshly_generated_strategy():
    with pytest.raises(StrategyLifecycleError):
        assert_executable("GENERATED")


def test_assert_executable_allows_approved_strategy():
    assert_executable("APPROVED")  # 예외가 발생하지 않으면 성공
