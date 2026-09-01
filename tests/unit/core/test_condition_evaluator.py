import pytest

from src.core.strategy.condition_evaluator import (
    ConditionEvaluationError,
    ConditionEvaluator,
    IndicatorDataMissingError,
    extract_indicator_keys,
)


@pytest.fixture
def evaluator() -> ConditionEvaluator:
    return ConditionEvaluator()


def test_simple_comparison_operators(evaluator: ConditionEvaluator):
    assert evaluator.evaluate("RSI > 30", {"RSI": 31.0}, None) is True
    assert evaluator.evaluate("RSI > 30", {"RSI": 30.0}, None) is False
    assert evaluator.evaluate("RSI >= 30", {"RSI": 30.0}, None) is True
    assert evaluator.evaluate("RSI < 30", {"RSI": 29.0}, None) is True
    assert evaluator.evaluate("RSI <= 30", {"RSI": 30.0}, None) is True
    assert evaluator.evaluate("RSI == 30", {"RSI": 30.0}, None) is True


def test_and_combination_requires_all(evaluator: ConditionEvaluator):
    market_state = {"RSI": 31.0, "SMA_timeperiod20": 45000.0}
    assert evaluator.evaluate("RSI > 30 AND SMA_timeperiod20 < 46000", market_state, None) is True
    assert evaluator.evaluate("RSI > 30 AND SMA_timeperiod20 < 44000", market_state, None) is False


def test_or_combination_requires_any(evaluator: ConditionEvaluator):
    market_state = {"RSI": 10.0, "SMA_timeperiod20": 45000.0}
    assert evaluator.evaluate("RSI > 30 OR SMA_timeperiod20 < 46000", market_state, None) is True
    assert evaluator.evaluate("RSI > 30 OR SMA_timeperiod20 > 46000", market_state, None) is False


def test_crosses_above_requires_prev_tick(evaluator: ConditionEvaluator):
    # 직전 틱 캐시가 없으면(첫 틱) 항상 False — 안전한 기본값.
    assert evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, None) is False
    assert (
        evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 29.0}) is True
    )
    assert (
        evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 32.0}) is False
    )


def test_crosses_below_requires_prev_tick(evaluator: ConditionEvaluator):
    assert evaluator.evaluate("RSI CROSSES_BELOW 30", {"RSI": 29.0}, None) is False
    assert evaluator.evaluate("RSI CROSSES_BELOW 30", {"RSI": 29.0}, {"RSI": 31.0}) is True


def test_missing_indicator_key_raises_data_missing(evaluator: ConditionEvaluator):
    with pytest.raises(IndicatorDataMissingError):
        evaluator.evaluate("RSI > 30", {}, None)


def test_malformed_expression_raises_evaluation_error(evaluator: ConditionEvaluator):
    with pytest.raises(ConditionEvaluationError):
        evaluator.evaluate("this is not valid", {}, None)


def test_extract_indicator_keys_single_condition():
    assert extract_indicator_keys("RSI > 30") == ["RSI"]


def test_extract_indicator_keys_and_combination():
    assert extract_indicator_keys("RSI > 30 AND SMA_timeperiod20 < 45000") == [
        "RSI",
        "SMA_timeperiod20",
    ]


def test_extract_indicator_keys_or_combination():
    assert extract_indicator_keys("RSI > 30 OR SMA_timeperiod20 < 45000") == [
        "RSI",
        "SMA_timeperiod20",
    ]
