"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 —
Runtime value and IR type-annotation consistency check (`check_value`).

Implements "interpretation of type annotations" from the `interpreter.py` module
docstring: annotations are the lower bound for domain (int/float/bool) and
"series-ness". `series<*>` annotation → must be `Series`; `int` annotation →
must be a scalar int; `float`/`bool` annotation → scalar or (if bar-dependent)
`Series`. Series must have length equal to the bar count, and elements must
fall within the domain. All mismatches raise `ScriptRuntimeError` (fail-closed).
Called at host inputs, builtin return values, and declaration boundaries
(let/signal/plot/order).
"""
from __future__ import annotations

from src.core.script.runtime.series import ScriptRuntimeError, Series, Value
from src.core.script.typing.types import Type, is_series


def check_value(value: Value, type_: Type, bar_count: int, where: str) -> Value:
    """Verify that `value` matches the domain and shape of annotation `type_`, then return it.

    A scalar `float` annotation promotes int values to float (upcast within the
    numeric domain). All other mismatches are errors.
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
