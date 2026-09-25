import re

import pytest

from src.core.strategy import condition_evaluator as condition_evaluator_module
from src.core.strategy.condition_evaluator import (
    ConditionEvaluationError,
    ConditionEvaluator,
    IndicatorDataMissingError,
    extract_indicator_keys,
)
from src.core.strategy.indicator_key import IndicatorKeyError


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


def test_empty_expression_raises_evaluation_error(evaluator: ConditionEvaluator):
    with pytest.raises(ConditionEvaluationError):
        evaluator.evaluate("", {}, None)


def test_extract_indicator_keys_with_lowercase_indicator_raises_key_error():
    # 컴파일러가 만들 수 없는 형태(indicator_key 문법 위반) — parse_key의
    # IndicatorKeyError가 그대로 전파되어야 한다(컴파일러 버그 신호).
    with pytest.raises(IndicatorKeyError):
        extract_indicator_keys("rsi > 30")


def test_extract_indicator_keys_propagates_parse_key_failure(monkeypatch: pytest.MonkeyPatch):
    def _boom(key: str) -> None:
        raise IndicatorKeyError(f"injected failure for {key!r}")

    monkeypatch.setattr(condition_evaluator_module, "parse_key", _boom)

    with pytest.raises(IndicatorKeyError):
        extract_indicator_keys("RSI > 30")


def test_unsupported_operator_raises_evaluation_error(
    evaluator: ConditionEvaluator, monkeypatch: pytest.MonkeyPatch
):
    # _ATOMIC_RE가 실제로는 알려진 연산자만 매칭시키므로, 공개 API로는
    # 도달할 수 없는 방어 분기(라인 107) — 컴파일러 문법이 확장돼도
    # ConditionEvaluator가 조용히 틀린 값을 반환하지 않고 실패해야 함을 검증.
    permissive_re = re.compile(
        r"^(?P<key>\S+)\s+(?P<op>\S+)\s+(?P<threshold>-?\d+(?:\.\d+)?)$"
    )
    monkeypatch.setattr(condition_evaluator_module, "_ATOMIC_RE", permissive_re)

    with pytest.raises(ConditionEvaluationError):
        evaluator.evaluate("RSI XOR 30", {"RSI": 31.0}, None)
