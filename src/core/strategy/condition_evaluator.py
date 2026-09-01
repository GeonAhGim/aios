"""FD-8.1 — ConditionEvaluator (ConditionCompiler의 역).

Spec: 기능설계문서_v1.21.md#FD-8.1 처리단계 3, src/services/condition_compiler.py

ConditionCompiler가 만든 문자열("{indicator}{params_suffix} {연산자} {threshold}"를
" AND "/" OR "로 결합)을 그대로 파싱해 market_state 딕셔너리에 대입 평가한다.
문법은 컴파일러가 고정한 형태만 지원 — 이 세션이 임의 표현식 파서를
새로 설계하지 않고, 컴파일러가 실제로 만드는 문자열만 정확히 되돌린다.
"""
from __future__ import annotations

import re

_ATOMIC_RE = re.compile(
    r"^(?P<key>\S+)\s+(?P<op>>=|<=|==|>|<|CROSSES_ABOVE|CROSSES_BELOW)\s+(?P<threshold>-?\d+(?:\.\d+)?)$"
)


class ConditionEvaluationError(Exception):
    """컴파일러가 만들 수 없는 형태의 문자열 — 컴파일러 자체의 버그 신호."""


class IndicatorDataMissingError(Exception):
    """market_state에 조건식이 참조하는 지표 키가 없음(FD-8.1 예외상황) —
    판단 보류 대상이지 오류가 아니다. 어떤 키가 없었는지 메시지에 담는다."""


def extract_indicator_keys(expression: str) -> list[str]:
    """실행 루프(오케스트레이터)가 market_state를 채우기 전에 "이 조건식이
    어떤 지표 키를 참조하는가"를 알아야 한다 — 컴파일러가 만든 키 형식을
    거꾸로 훑는 이 함수가 그 단일 출처다(ConditionEvaluator와 동일 문법
    가정)."""
    if " AND " in expression:
        parts = expression.split(" AND ")
    elif " OR " in expression:
        parts = expression.split(" OR ")
    else:
        parts = [expression]

    keys = []
    for part in parts:
        match = _ATOMIC_RE.match(part.strip())
        if match is not None:
            keys.append(match["key"])
    return keys


class ConditionEvaluator:
    def evaluate(
        self,
        expression: str,
        market_state: dict[str, float],
        prev_market_state: dict[str, float] | None,
    ) -> bool:
        if " AND " in expression:
            return all(
                self._evaluate_atomic(part, market_state, prev_market_state)
                for part in expression.split(" AND ")
            )
        if " OR " in expression:
            return any(
                self._evaluate_atomic(part, market_state, prev_market_state)
                for part in expression.split(" OR ")
            )
        return self._evaluate_atomic(expression, market_state, prev_market_state)

    def _evaluate_atomic(
        self,
        atomic: str,
        market_state: dict[str, float],
        prev_market_state: dict[str, float] | None,
    ) -> bool:
        match = _ATOMIC_RE.match(atomic.strip())
        if match is None:
            raise ConditionEvaluationError(f"조건식을 해석할 수 없습니다: {atomic!r}")

        key = match["key"]
        if key not in market_state:
            raise IndicatorDataMissingError(key)

        value = market_state[key]
        op = match["op"]
        threshold = float(match["threshold"])

        if op == ">":
            return value > threshold
        if op == "<":
            return value < threshold
        if op == ">=":
            return value >= threshold
        if op == "<=":
            return value <= threshold
        if op == "==":
            return value == threshold

        prev_value = None if prev_market_state is None else prev_market_state.get(key)
        if op == "CROSSES_ABOVE":
            return prev_value is not None and prev_value <= threshold < value
        if op == "CROSSES_BELOW":
            return prev_value is not None and prev_value >= threshold > value
        raise ConditionEvaluationError(f"지원하지 않는 연산자입니다: {op}")
