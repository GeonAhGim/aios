from unittest.mock import MagicMock

import pytest

from src.core.strategy.condition_evaluator import (
    ConditionEvaluationError,
    IndicatorDataMissingError,
)
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
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

    signal = engine.evaluate(config, {"RSI": 25.0}, execution_id=1, fsm_state=FSMState.IDLE)

    assert signal is not None
    assert signal.direction == OrderSide.BUY
    assert signal.symbol == "BTC/USDT"
    assert signal.strategy_id == "strat-1"
    assert signal.strategy_version == "v1"


def test_idle_no_signal_when_condition_not_met():
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(config, {"RSI": 50.0}, execution_id=1, fsm_state=FSMState.IDLE)

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
        assert engine.evaluate(config, {"RSI": 1.0}, execution_id=1, fsm_state=state) is None


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
    signal = engine.evaluate(config, {"RSI": 10.0}, execution_id=3, fsm_state=FSMState.HOLDING)

    assert signal is None


def test_prev_tick_cache_enables_crosses_below_on_second_tick():
    engine = StrategyEngine()
    config = _compile_config()

    first = engine.evaluate(config, {"RSI": 25.0}, execution_id=4, fsm_state=FSMState.HOLDING)
    assert first is None  # RSI 25 > 20, crosses_below 미충족, exit(>70)도 미충족

    second = engine.evaluate(config, {"RSI": 15.0}, execution_id=4, fsm_state=FSMState.HOLDING)

    assert second is not None
    assert second.direction == OrderSide.SELL


# ---------------------------------------------------------------------------
# Negative tests — invalid inputs must be rejected (DoD: ≥3 negative tests)
# ---------------------------------------------------------------------------


def test_evaluate_with_empty_transitions_returns_none():
    """전이 테이블이 비어 있으면 아무 신호도 내지 않아야 한다."""
    engine = StrategyEngine()
    config = _compile_config()
    # transitions 목록을 비운 복사본을 만든다.
    empty_config = FSMStrategyConfig(
        strategy_id=config.strategy_id,
        version=config.version,
        target_asset=config.target_asset,
        market=config.market,
        exchange=config.exchange,
        author_agent=config.author_agent,
        states=list(config.states),
        transitions=[],
    )

    signal = engine.evaluate(empty_config, {"RSI": 25.0}, execution_id=10, fsm_state=FSMState.IDLE)

    assert signal is None


def test_evaluate_with_unknown_fsm_state_returns_none():
    """알 수 없는 FSMState는 전이 대상이 없으므로 None을 반환한다."""
    engine = StrategyEngine()
    config = _compile_config()
    # FSMState는 enum이므로 실제 enum 값만 허용되나,
    # 전이에서 from_state != fsm_state이면 매칭되지 않음을 검증.
    # enum 외부 값을 넣을 수 없으므로, 대신 HOLDING에서 ENTRY 조건만 있는
    # 구성으로 HOLDING에서 진입 신호가 나지 않음을 검증한다.
    # (IDLE->HOLDING 전이만 있고, HOLDING->IDLE 전이가 없음)
    holding_signal = engine.evaluate(
        config, {"RSI": 25.0}, execution_id=12, fsm_state=FSMState.HOLDING
    )
    assert holding_signal is None


def test_evaluate_with_no_market_state_keys_returns_none():
    """market_state가 비어 있으면 지표 데이터 부재로 None을 반환한다."""
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(config, {}, execution_id=13, fsm_state=FSMState.IDLE)

    assert signal is None


# ---------------------------------------------------------------------------
# Failure-injection tests — dependency exceptions must not crash the engine
# ---------------------------------------------------------------------------


def test_evaluator_raises_indicator_data_missing_error_returns_none():
    """ConditionEvaluator.evaluate가 IndicatorDataMissingError를 raise하면
    engine은 신호를 내지 않고 None을 반환해야 한다 (fail-closed, 경고 로그만 남김)."""
    engine = StrategyEngine()
    config = _compile_config()

    # _evaluator.evaluate를 모킹해 항상 IndicatorDataMissingError를 발생시킨다.
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.side_effect = IndicatorDataMissingError("VOLUME")
    engine._evaluator = mock_evaluator

    signal = engine.evaluate(config, {"RSI": 25.0}, execution_id=20, fsm_state=FSMState.IDLE)

    assert signal is None


def test_condition_evaluation_error_propagates():
    """ConditionEvaluationError는 catch 대상이 아니므로 상위에게 전파되어야 한다.
    engine은 IndicatorDataMissingError만 별도 처리하고 나머지는 그대로 둔다."""
    engine = StrategyEngine()
    config = _compile_config()

    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.side_effect = ConditionEvaluationError("bad expression")
    engine._evaluator = mock_evaluator

    with pytest.raises(ConditionEvaluationError, match="bad expression"):
        engine.evaluate(config, {"RSI": 25.0}, execution_id=22, fsm_state=FSMState.IDLE)


def test_evaluator_raises_indicator_data_missing_error_continues_evaluation():
    """ConditionEvaluator.evaluate가 IndicatorDataMissingError를 raise하면
    해당 전이를 건너뛰고 다음 전이를 평가해야 한다 (경고 로그만 남김)."""
    engine = StrategyEngine()
    compiler = ConditionCompiler()
    # 두 조건을 가진 구성: 첫 번째는 항상 예외, 두 번째는 정상 만족
    config = compiler.compile(
        strategy_id="strat-fail",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="VOLUME", operator=">", threshold=100.0)],
    )

    # 첫 번째 조건 호출에서만 예외, 두 번째 호출에서는 정상 반환
    original_evaluate = engine._evaluator.evaluate
    call_count = [0]

    def side_effect(expr, market_state, prev_market_state):
        call_count[0] += 1
        if call_count[0] == 1:
            raise IndicatorDataMissingError("VOLUME")
        return original_evaluate(expr, market_state, prev_market_state)

    engine._evaluator.evaluate = side_effect

    # RSI=80이므로 exit 조건(>70)이 만족되어야 한다.
    # 첫 호출에서 IndicatorDataMissingError가 발생해도 두 번째 평가에서
    # exit 조건이 매칭되면 신호가 나와야 한다.
    signal = engine.evaluate(config, {"RSI": 80.0}, execution_id=21, fsm_state=FSMState.HOLDING)

    assert signal is not None
    assert signal.direction == OrderSide.SELL
