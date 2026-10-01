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
    assert evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 29.0}) is True
    assert evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 32.0}) is False


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
    permissive_re = re.compile(r"^(?P<key>\S+)\s+(?P<op>\S+)\s+(?P<threshold>-?\d+(?:\.\d+)?)$")
    monkeypatch.setattr(condition_evaluator_module, "_ATOMIC_RE", permissive_re)

    with pytest.raises(ConditionEvaluationError):
        evaluator.evaluate("RSI XOR 30", {"RSI": 31.0}, None)


def test_and_combination_missing_second_key_raises_data_missing(
    evaluator: ConditionEvaluator,
):
    # AND는 좌항이 참이어도 우항 지표가 없으면 조용히 넘어가지 않고
    # IndicatorDataMissingError로 실패해야 한다(fail-closed) — `all()`의
    # 제너레이터 단축평가가 두 번째 atomic 평가를 건너뛰지 않는지 검증.
    market_state = {"RSI": 31.0}
    with pytest.raises(IndicatorDataMissingError) as exc_info:
        evaluator.evaluate("RSI > 30 AND SMA_timeperiod20 < 46000", market_state, None)
    assert exc_info.value.args[0] == "SMA_timeperiod20"


def test_crosses_above_prev_state_get_failure_propagates(
    evaluator: ConditionEvaluator,
):
    # prev_market_state가 dict 계약을 어기는 의존성(예: 캐시 계층 예외)일 때
    # 조용히 삼키지 말고 그대로 전파해야 한다(fail-closed 실패주입).
    class _BoomDict(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("injected prev-tick cache failure")

    with pytest.raises(RuntimeError, match="injected prev-tick cache failure"):
        evaluator.evaluate("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, _BoomDict())


@pytest.mark.perf
def test_evaluate_stays_within_budget_for_repeated_calls(
    evaluator: ConditionEvaluator, perf_budget
):
    """수치 성능 단언(D2) — 조건식 재컴파일 없이 반복 평가되는 실행 루프
    경로이므로 호출당 비용이 정규식 매칭 수준에 머물러야 한다. O(n^2) 등으로
    회귀하면 이 단언이 실패한다."""
    market_state = {"RSI": 31.0, "SMA_timeperiod20": 45000.0}
    prev_state = {"RSI": 29.0, "SMA_timeperiod20": 44000.0}
    repeats = 500

    def _run() -> None:
        for _ in range(repeats):
            evaluator.evaluate("RSI > 30 AND SMA_timeperiod20 < 46000", market_state, prev_state)
            evaluator.evaluate("RSI CROSSES_ABOVE 30", market_state, prev_state)

    sample = perf_budget.best_of(_run)
    per_call_ms = sample.cpu_ms / (repeats * 2)
    budget_ms = 1.0  # 순수 정규식 매칭 + dict 조회 — 1ms/call이면 넉넉한 여유치
    assert per_call_ms < budget_ms, (
        f"evaluate() averaged {per_call_ms:.4f}ms/call over {repeats * 2} calls, "
        f"budget is {budget_ms}ms/call"
    )
