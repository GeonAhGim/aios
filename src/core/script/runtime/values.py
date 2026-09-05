"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 —
런타임 값과 IR 타입 주석의 정합 검사(`check_value`).

`interpreter.py` 모듈 docstring의 "타입 주석의 해석"을 구현한다: 주석은 도메인
(int/float/bool)과 "시리즈임"의 하한이다. `series<*>` 주석 → 반드시 `Series`,
`int` 주석 → 반드시 스칼라 int, `float`/`bool` 주석 → 스칼라 또는 (봉 의존이면)
`Series`. 시리즈는 길이가 봉 수와 같아야 하고 원소는 도메인 안이어야 한다.
불일치는 전부 `ScriptRuntimeError`(fail-closed). 호스트 입력·빌트인 반환값·
decl 경계(let/signal/plot/order)에서 호출된다.
"""
from __future__ import annotations

from src.core.script.runtime.series import ScriptRuntimeError, Series, Value
from src.core.script.typing.types import Type, is_series


def check_value(value: Value, type_: Type, bar_count: int, where: str) -> Value:
    """`value`가 주석 `type_`의 도메인·모양에 맞는지 확인하고 반환한다.

    스칼라 `float` 주석은 int 값을 float로 올린다(수치 도메인 안의 승격). 그 외
    불일치는 전부 오류.
    """
    if isinstance(value, Series):
        if type_ == "int":
            raise ScriptRuntimeError(f"{where}: int 주석 자리에 시리즈가 왔습니다")
        if len(value) != bar_count:
            raise ScriptRuntimeError(f"{where}: 시리즈 길이 {len(value)} != 봉 수 {bar_count}")
        checker = _bool_element if type_ in ("bool", "series<bool>") else _float_element
        for i, v in enumerate(value.values):
            if not checker(v):
                raise ScriptRuntimeError(f"{where}: 원소 #{i}가 {type_} 도메인 밖입니다: {v!r}")
        return value
    if is_series(type_):
        raise ScriptRuntimeError(f"{where}: {type_} 주석 자리에 스칼라 {value!r}가 왔습니다")
    if type_ == "bool":
        if not _bool_element(value):
            raise ScriptRuntimeError(f"{where}: bool 자리에 {value!r}")
        return value
    if type_ == "int":
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise ScriptRuntimeError(f"{where}: int 자리에 {value!r}")
        return value
    if not _float_element(value):
        raise ScriptRuntimeError(f"{where}: float 자리에 {value!r}")
    return None if value is None else float(value)


def _bool_element(v: Value) -> bool:
    return v is None or isinstance(v, bool)


def _float_element(v: Value) -> bool:
    return v is None or (isinstance(v, int | float) and not isinstance(v, bool))
