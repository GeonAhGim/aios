from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition


def test_fsm_strategy_config_roundtrip():
    config = FSMStrategyConfig(
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
    assert config.initial_state == FSMState.IDLE
    assert config.memory_provenance == []
    assert config.transitions[0].to_state == FSMState.HOLDING
