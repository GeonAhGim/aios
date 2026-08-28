"""14.5 통합테스트 — 조건조합→FSM 컴파일→9.11 스키마 검증 왕복.

실제 dev DB 대상 — 컴파일된 FSMStrategyConfig를 14.3(StrategyBuilderService)
으로 저장했다가 다시 읽어와도 9.11 스키마를 그대로 만족함을 검증한다.
"""
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.services.condition_compiler import (
    ORDER_FILLED,
    ConditionCompileError,
    ConditionCompiler,
)
from src.services.preview_service import PreviewCondition
from src.services.strategy_builder_service import StrategyBuilderService
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
def compiler():
    return ConditionCompiler()


def _sample_kwargs(strategy_id: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "version": "1.0.0",
        "target_asset": "BTC/USDT",
        "market": "crypto",
        "exchange": "bitget",
        "author_agent": "user",
        "entry_conditions": [
            PreviewCondition(indicator="RSI", params={"timeperiod": 14}, operator="<", threshold=30)
        ],
        "exit_conditions": [
            PreviewCondition(indicator="RSI", params={"timeperiod": 14}, operator=">", threshold=70)
        ],
        "stop_loss_conditions": [
            PreviewCondition(indicator="ATR", params={"timeperiod": 14}, operator=">", threshold=5)
        ],
    }


def test_compile_produces_valid_fsm_schema(compiler):
    config = compiler.compile(**_sample_kwargs(f"test-{uuid4().hex[:8]}"))

    assert isinstance(config, FSMStrategyConfig)
    assert config.initial_state == FSMState.IDLE
    assert len(config.states) == 6
    assert len(config.transitions) == 6


def test_compiled_conditions_embedded_in_correct_transitions(compiler):
    config = compiler.compile(**_sample_kwargs(f"test-{uuid4().hex[:8]}"))

    entry_transition = next(
        t for t in config.transitions
        if t.from_state == FSMState.IDLE and t.to_state == FSMState.BUY_ORDER_PENDING
    )
    assert "RSI_timeperiod14 < 30.0" == entry_transition.condition

    exit_transition = next(
        t for t in config.transitions
        if t.from_state == FSMState.HOLDING and t.to_state == FSMState.SELL_ORDER_PENDING
    )
    assert "RSI_timeperiod14 > 70.0" == exit_transition.condition

    order_filled_transitions = [t for t in config.transitions if t.condition == ORDER_FILLED]
    assert len(order_filled_transitions) == 3


def test_compile_rejects_whitelisted_asset_violation(compiler):
    kwargs = _sample_kwargs(f"test-{uuid4().hex[:8]}")
    kwargs["target_asset"] = "NOT/LISTED"

    with pytest.raises(ConditionCompileError):
        compiler.compile(**kwargs)


def test_compile_rejects_empty_condition_group(compiler):
    kwargs = _sample_kwargs(f"test-{uuid4().hex[:8]}")
    kwargs["entry_conditions"] = []

    with pytest.raises(ConditionCompileError):
        compiler.compile(**kwargs)


def test_compile_rejects_unsupported_operator(compiler):
    kwargs = _sample_kwargs(f"test-{uuid4().hex[:8]}")
    kwargs["exit_conditions"] = [
        PreviewCondition.model_construct(indicator="RSI", params={}, operator="~=", threshold=1)
    ]

    with pytest.raises(ConditionCompileError):
        compiler.compile(**kwargs)


async def test_full_round_trip_compile_save_reload_revalidates(compiler, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-{uuid4().hex[:8]}"
    config = compiler.compile(**_sample_kwargs(strategy_id))

    builder = StrategyBuilderService(pool)
    await builder.save_strategy(
        owner,
        config.strategy_id,
        config.version,
        target_asset=config.target_asset,
        market=config.market,
        exchange=config.exchange,
        fsm_definition=config.model_dump(mode="json"),
        author_agent=config.author_agent,
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fsm_definition, lifecycle_status FROM strategies "
            "WHERE strategy_id = $1 AND version = $2",
            strategy_id,
            "1.0.0",
        )

    reloaded = FSMStrategyConfig(**json.loads(row["fsm_definition"]))
    assert reloaded == config
    assert row["lifecycle_status"] == "GENERATED"
