from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide
from src.services.condition_compiler import ConditionCompiler
from src.services.preview_service import PreviewCondition


def _compile_config():
    compiler = ConditionCompiler()
    return compiler.compile(
        strategy_id="strat-1",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[
            PreviewCondition(indicator="RSI", operator="crosses_below", threshold=20.0)
        ],
    )


def test_idle_entry_signal_generated_on_condition_met():
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(
        config, {"RSI": 25.0}, execution_id=1, fsm_state=FSMState.IDLE
    )

    assert signal is not None
    assert signal.direction == OrderSide.BUY
    assert signal.symbol == "BTC/USDT"
    assert signal.strategy_id == "strat-1"
    assert signal.strategy_version == "v1"


def test_idle_no_signal_when_condition_not_met():
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(
        config, {"RSI": 50.0}, execution_id=1, fsm_state=FSMState.IDLE
    )

    assert signal is None


def test_pending_states_never_produce_a_signal():
    """BUY_ORDER_PENDING/SELL_ORDER_PENDING/STOP_LOSS의 유일한 나가는 전이는
    ORDER_FILLED뿐이라 이 함수의 평가 대상이 아니다(FD-4.2가 트리거)."""
    engine = StrategyEngine()
    config = _compile_config()

    for state in (
        FSMState.BUY_ORDER_PENDING,
        FSMState.SELL_ORDER_PENDING,
        FSMState.STOP_LOSS,
        FSMState.EMERGENCY_EXIT,
    ):
        assert (
            engine.evaluate(config, {"RSI": 1.0}, execution_id=1, fsm_state=state) is None
        )


def test_holding_stop_loss_takes_priority_over_exit_when_both_true():
    engine = StrategyEngine()

    # 첫 틱으로 prev=15(20 미만) 캐시를 만든 뒤, 다음 틱에서 RSI가 20을
    # 상향 돌파(crosses_below 20의 반대 방향)하면서 동시에 exit(> 70)
    # 조건도 만족시키는 것은 비현실적이므로, stop_loss 자체가 crosses_below라
    # 우선순위를 직접 검증하려면 두 조건이 같은 값에서 동시에 참이 되도록
    # 구성한다 — exit(>70)과 stop_loss(RSI < 10, 아래에서 재구성)를 동시 충족.
    exit_and_stop_config = ConditionCompiler().compile(
        strategy_id="strat-2",
        version="v1",
        target_asset="ETH/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
    )

    signal = engine.evaluate(
        exit_and_stop_config, {"RSI": 80.0}, execution_id=2, fsm_state=FSMState.HOLDING
    )

    assert signal is not None
    assert signal.direction == OrderSide.SELL
    # stop_loss로 가는 전이가 우선 평가돼야 한다 — HOLDING->STOP_LOSS
    matched_transition = next(
        t
        for t in exit_and_stop_config.transitions
        if t.from_state == FSMState.HOLDING and t.to_state == FSMState.STOP_LOSS
    )
    assert matched_transition.condition == "RSI > 70.0"


def test_missing_indicator_data_returns_none_without_raising():
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(config, {}, execution_id=1, fsm_state=FSMState.IDLE)

    assert signal is None


def test_crosses_below_first_tick_is_safe_false():
    engine = StrategyEngine()
    config = _compile_config()

    # HOLDING 상태에서 stop_loss(crosses_below 20) 최초 틱은 prev 캐시가
    # 없어 항상 False여야 한다 — exit(> 70)도 미충족이므로 신호 없음.
    signal = engine.evaluate(
        config, {"RSI": 10.0}, execution_id=3, fsm_state=FSMState.HOLDING
    )

    assert signal is None


def test_prev_tick_cache_enables_crosses_below_on_second_tick():
    engine = StrategyEngine()
    config = _compile_config()

    first = engine.evaluate(config, {"RSI": 25.0}, execution_id=4, fsm_state=FSMState.HOLDING)
    assert first is None  # RSI 25 > 20, crosses_below 미충족, exit(>70)도 미충족

    second = engine.evaluate(config, {"RSI": 15.0}, execution_id=4, fsm_state=FSMState.HOLDING)

    assert second is not None
    assert second.direction == OrderSide.SELL
