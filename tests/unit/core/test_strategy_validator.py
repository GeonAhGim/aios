from src.core.validator.strategy_validator import validate_strategy_config
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition


def _config(**overrides) -> FSMStrategyConfig:
    defaults = dict(
        strategy_id="strat-1",
        version="v1.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.HOLDING],
        transitions=[
            FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="rsi<30")
        ],
        author_agent="strategy-research-agent",
    )
    defaults.update(overrides)
    return FSMStrategyConfig(**defaults)


def test_valid_fsm_passes():
    result = validate_strategy_config(_config())
    assert result.is_valid is True
    assert result.errors == []


def test_initial_state_not_in_states_rejected():
    result = validate_strategy_config(
        _config(states=[FSMState.HOLDING], initial_state=FSMState.IDLE, transitions=[])
    )
    assert result.is_valid is False
    assert any("initial_state" in e for e in result.errors)


def test_orphan_state_rejected():
    result = validate_strategy_config(
        _config(states=[FSMState.IDLE, FSMState.HOLDING, FSMState.STOP_LOSS])
    )
    assert result.is_valid is False
    assert any("고아 state" in e for e in result.errors)


def test_self_loop_rejected():
    result = validate_strategy_config(
        _config(
            states=[FSMState.IDLE],
            initial_state=FSMState.IDLE,
            transitions=[
                FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.IDLE, condition="x")
            ],
        )
    )
    assert result.is_valid is False
    assert any("자기순환" in e for e in result.errors)


def test_duplicate_transition_rejected():
    dup = FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="rsi<30")
    result = validate_strategy_config(_config(transitions=[dup, dup]))
    assert result.is_valid is False
    assert any("중복 transition" in e for e in result.errors)


def test_transition_references_undeclared_state_rejected():
    result = validate_strategy_config(
        _config(
            states=[FSMState.IDLE],
            initial_state=FSMState.IDLE,
            transitions=[
                FSMTransition(
                    from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="rsi<30"
                )
            ],
        )
    )
    assert result.is_valid is False
    assert any("to_state" in e and "states 목록에 없습니다" in e for e in result.errors)
