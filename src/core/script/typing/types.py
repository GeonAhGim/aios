"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-4 —
AIOS Script 정적 타입 격자.

§3.3 `type := "int" | "float" | "bool" | "series<float>" | "series<bool>"`의
5종 타입에 대해 "시리즈/스칼라 승격·거부"(DoD) 규칙만 순수 함수로 정의한다.
AST 순회·decl별 검사는 `checker.py`(같은 리프)의 몫이라 이 모듈은 I/O도
`ast.py` 임포트도 하지 않는다 — 타입 이름(`TypeName`) 자체만 재사용한다.

승격 격자:
    int ≤ float ≤ series<float>   (수치 계열 — 산술·비교 피연산자)
    bool ≤ series<bool>            (불리언 계열 — 논리 연산 피연산자)
두 계열은 서로 섞이지 않는다(bool과 수치 사이 암묵적 변환 없음 — "거부").
"""
from __future__ import annotations

from src.core.script.grammar.ast import TypeName

Type = TypeName

NUMERIC_TYPES: frozenset[Type] = frozenset({"int", "float", "series<float>"})
BOOL_TYPES: frozenset[Type] = frozenset({"bool", "series<bool>"})

_SERIES_TYPES: frozenset[Type] = frozenset({"series<float>", "series<bool>"})
_SERIES_ELEMENT: dict[Type, Type] = {"series<float>": "float", "series<bool>": "bool"}


def is_series(type_: Type) -> bool:
    """`type_`이 시리즈 계열(series<float>/series<bool>)인지."""
    return type_ in _SERIES_TYPES


def element_type(type_: Type) -> Type:
    """시리즈 타입의 원소 타입(series<float> -> float, series<bool> -> bool).

    스칼라 타입이 들어오면 그대로 반환한다(호출자가 `is_series`로 먼저
    분기하는 것이 기본이지만, 이 함수 자체는 부분함수로 만들지 않는다).
    """
    return _SERIES_ELEMENT.get(type_, type_)


def promote_numeric(left: Type, right: Type) -> Type | None:
    """산술(+·-·*·/) 결과 타입. 승격: series<float> > float > int.

    둘 중 하나라도 수치 계열(`NUMERIC_TYPES`)이 아니면 승격이 아니라
    거부이므로 `None`을 반환한다(예: bool과의 산술).
    """
    if left not in NUMERIC_TYPES or right not in NUMERIC_TYPES:
        return None
    if "series<float>" in (left, right):
        return "series<float>"
    if "float" in (left, right):
        return "float"
    return "int"


def promote_bool(left: Type, right: Type) -> Type | None:
    """논리(and·or) 결과 타입. 승격: series<bool> > bool.

    둘 중 하나라도 불리언 계열(`BOOL_TYPES`)이 아니면 `None`(거부).
    """
    if left not in BOOL_TYPES or right not in BOOL_TYPES:
        return None
    return "series<bool>" if "series<bool>" in (left, right) else "bool"


def cmp_result(left: Type, right: Type) -> Type | None:
    """비교(<·<=·==·>=·>·crosses_above·crosses_below) 결과 타입.

    두 피연산자 모두 수치 계열이어야 한다(`None`이면 거부). 결과는 둘 중
    하나라도 시리즈면 `series<bool>`(바마다 판정), 둘 다 스칼라면 `bool`.
    """
    if left not in NUMERIC_TYPES or right not in NUMERIC_TYPES:
        return None
    return "series<bool>" if is_series(left) or is_series(right) else "bool"
