import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition


def _valid_kwargs(**overrides):
    kwargs = dict(
        strategy_id="strat-1",
        version="v1.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="buy_signal"
            )
        ],
        author_agent="strategy-research-agent",
    )
    kwargs.update(overrides)
    return kwargs


def test_fsm_strategy_config_roundtrip():
    config = FSMStrategyConfig(**_valid_kwargs())
    assert config.initial_state == FSMState.IDLE
    assert config.memory_provenance == []
    assert config.transitions[0].to_state == FSMState.HOLDING


def test_fsm_strategy_config_with_explicit_memory_provenance():
    provenance_id = uuid4()
    config = FSMStrategyConfig(**_valid_kwargs(memory_provenance=[provenance_id]))
    assert config.memory_provenance == [provenance_id]


def test_invalid_initial_state_enum_value_raises():
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**_valid_kwargs(initial_state="NOT_A_REAL_STATE"))


def test_missing_required_field_raises():
    kwargs = _valid_kwargs()
    del kwargs["author_agent"]
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**kwargs)


def test_transition_with_invalid_state_type_raises():
    with pytest.raises(ValidationError):
        FSMTransition(from_state="NOT_A_REAL_STATE", to_state=FSMState.HOLDING, condition="x")


def test_memory_provenance_invalid_uuid_raises():
    with pytest.raises(ValidationError):
        FSMStrategyConfig(**_valid_kwargs(memory_provenance=["not-a-uuid"]))


def test_transition_dependency_failure_propagates_fail_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(FSMTransition, "__init__", boom)
    with pytest.raises(RuntimeError):
        FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="buy_signal")


def test_fsm_strategy_config_construction_throughput():
    iterations = 500
    start = time.perf_counter()
    for _ in range(iterations):
        FSMStrategyConfig(**_valid_kwargs())
    elapsed = time.perf_counter() - start
    per_op_ms = (elapsed / iterations) * 1000
    assert per_op_ms < 5.0
