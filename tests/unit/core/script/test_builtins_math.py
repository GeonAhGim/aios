"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-9a —
`runtime/builtins_math.py` 테스트.

확인 항목: (1) `math.*` 표의 함수별 스칼라 의미(round half-away-from-zero 포함),
(2) 스칼라·시리즈 승격(DSL-4 격자: 하나라도 시리즈면 시리즈), (3) na 전파·비유한
결과→na가 DSL-8 `series.py` 규칙과 같음(시드 고정 무작위 property, 독립 참조 구현
대조 — hypothesis 미설치라 `random.Random`), (4) negative: 인자 개수·bool·길이
불일치는 `BuiltinCallError`(fail-closed), (5) I-10 배선: `default_builtins()`로
파싱→IR→실행 경로에서 실제 디스패치됨.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.ir import lower_program
from src.core.script.runtime import (
    MATH_BUILTINS,
    BuiltinCallError,
    CallSite,
    ScriptRuntimeError,
    Series,
    Value,
    default_builtins,
    execute,
)

SITE = CallSite("math", "x", "float", 4)
S = Series.of_floats([1.5, -2.5, None, 4.0])


def call(ident: str, *args: Value, bars: int = 4) -> Value:
    return MATH_BUILTINS[("math", ident)](args, CallSite("math", ident, "float", bars))


# ---- (1) 함수별 스칼라 의미 ----


@pytest.mark.parametrize(
    ("ident", "args", "expected"),
    [
        ("abs", (-3,), 3.0),
        ("sign", (-0.5,), -1.0),
        ("sign", (0.0,), 0.0),
        ("sign", (7,), 1.0),
        ("floor", (2.7,), 2.0),
        ("floor", (-2.1,), -3.0),
        ("ceil", (2.1,), 3.0),
        ("round", (0.5,), 1.0),
        ("round", (-0.5,), -1.0),
        ("round", (2.5,), 3.0),
        ("round", (2.4,), 2.0),
        ("sqrt", (16,), 4.0),
        ("log", (math.e,), 1.0),
        ("exp", (0,), 1.0),
        ("pow", (2, 10), 1024.0),
        ("max", (1, 5.5, 3), 5.5),
        ("min", (1, 5.5, -3), -3.0),
        ("nz", (None,), 0.0),
        ("nz", (None, 7), 7.0),
        ("nz", (2.5, 7), 2.5),
    ],
)
def test_scalar_semantics(ident: str, args: tuple[Value, ...], expected: float) -> None:
    result = call(ident, *args)
    assert isinstance(result, float) and result == expected


def test_table_contains_exactly_the_documented_functions() -> None:
    expected = {"abs", "sign", "floor", "ceil", "round", "sqrt", "log", "exp", "pow", "max",
                "min", "nz"}  # fmt: skip
    assert {ident for ns, ident in MATH_BUILTINS if ns == "math"} == expected
    assert all(ns == "math" for ns, _ in MATH_BUILTINS)


# ---- (2) 승격 ----


def test_series_argument_promotes_result_to_series_of_bar_count() -> None:
    assert call("abs", S) == Series((1.5, 2.5, None, 4.0))
    assert call("max", S, 2) == Series((2.0, 2.0, None, 4.0))
    assert call("pow", 2, S) == Series((2**1.5, 2**-2.5, None, 16.0))
    assert call("nz", S, -1) == Series((1.5, -2.5, -1.0, 4.0))
    assert call("nz", 3, S) == Series((3.0,) * 4)
    assert call("nz", None, S) == S


def test_all_scalar_arguments_keep_scalar_result_even_with_bar_count() -> None:
    assert call("abs", -1, bars=100) == 1.0
    assert call("nz", None, 2, bars=100) == 2.0


# ---- (3) na 전파·비유한 → na ----


@pytest.mark.parametrize(
    ("ident", "args"),
    [("log", (0,)), ("log", (-1,)), ("sqrt", (-1,)), ("exp", (1000,)), ("pow", (0, -1)),
     ("pow", (-8, 0.5)), ("pow", (10, 400))],
)  # fmt: skip
def test_non_finite_or_domain_error_yields_na_not_exception(
    ident: str, args: tuple[Value, ...]
) -> None:
    assert call(ident, *args) is None


def test_na_propagates_through_every_function_except_nz() -> None:
    for _ns, ident in MATH_BUILTINS:
        if ident == "nz":
            continue
        argc = 2 if ident in ("pow", "max", "min") else 1
        assert call(ident, *([None] * argc)) is None, ident
        assert call(ident, *([1.0] * (argc - 1)), None) is None, ident
    assert call("nz", None, None) is None


_REF: dict[str, Callable[[list[float]], float]] = {
    "abs": lambda r: abs(r[0]),
    "sign": lambda r: (r[0] > 0) - (r[0] < 0),
    "floor": lambda r: math.floor(r[0]),
    "ceil": lambda r: math.ceil(r[0]),
    "round": lambda r: math.copysign(math.floor(abs(r[0]) + 0.5), r[0]),
    "sqrt": lambda r: math.sqrt(r[0]),
    "log": lambda r: math.log(r[0]),
    "exp": lambda r: math.exp(r[0]),
    "pow": lambda r: math.pow(r[0], r[1]),
    "max": max,
    "min": min,
}


def _reference(ident: str, row: list[float | None]) -> float | None:
    """독립 참조: na 하나라도 → na, 예외·비유한 → na(DSL-8 `_finite` 규칙)."""
    if any(v is None for v in row):
        return None
    try:
        out = float(_REF[ident]([v for v in row if v is not None]))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return out if math.isfinite(out) else None


def test_property_na_propagation_and_promotion_match_reference() -> None:
    rng = random.Random(1554)
    bars = 6

    def scalar() -> float | None:
        if rng.random() < 0.25:
            return None
        return rng.choice([rng.uniform(-50, 50), 0.0, -1.0, 1e300, rng.randint(-3, 3)])

    for _ in range(300):
        ident = rng.choice(list(_REF))
        argc = rng.randint(2, 3) if ident in ("max", "min") else (2 if ident == "pow" else 1)
        args: list[Value] = [
            Series(tuple(scalar() for _ in range(bars))) if rng.random() < 0.5 else scalar()
            for _ in range(argc)
        ]
        result = call(ident, *args, bars=bars)
        if any(isinstance(a, Series) for a in args):
            assert isinstance(result, Series) and len(result) == bars
            for t in range(bars):
                row = [a.values[t] if isinstance(a, Series) else a for a in args]
                assert result.values[t] == _reference(ident, [_f(v) for v in row]), (ident, args)
        else:
            assert result == _reference(ident, [_f(a) for a in args]), (ident, args)


def _f(v: Value) -> float | None:
    assert not isinstance(v, Series)
    return None if v is None else float(v)


# ---- (4) negative ----


@pytest.mark.parametrize(
    ("ident", "args"),
    [("abs", ()), ("abs", (1, 2)), ("pow", (2,)), ("max", (1,)), ("nz", ()), ("nz", (1, 2, 3))],
)
def test_wrong_arity_is_rejected(ident: str, args: tuple[Value, ...]) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call(ident, *args)
    assert info.value.reason == "SCRIPT_BUILTIN_ARITY"


def test_bool_operands_and_shape_mismatch_are_rejected() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("abs", True)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"
    with pytest.raises(BuiltinCallError):
        call("max", S, Series.of_bools([True, False, None, True]))
    with pytest.raises(BuiltinCallError, match="길이"):
        call("abs", Series.of_floats([1.0, 2.0]))
    with pytest.raises(ScriptRuntimeError):
        call("nz", Series.of_floats([1.0, 2.0]), 0)
    assert issubclass(BuiltinCallError, ScriptRuntimeError)


# ---- (5) 배선: 파싱→IR→execute 경로에서 default_builtins 표로 디스패치 ----


def test_wired_through_interpreter_with_default_builtins() -> None:
    src = (
        "input close: series<float> = 0\n"
        "let d = math.abs(close - 3)\n"
        "let m = math.max(close, 2.5)\n"
        "let z = math.nz(close[1], -1)\n"
        "let f = math.floor(2.7)\n"
        "signal ok = math.sqrt(close) > 1.5"
    )
    close = Series.of_floats([1, 4, 2, 9])
    result = execute(lower_program(parse(src)), bar_count=4, inputs={"close": close},
                     builtins=default_builtins())  # fmt: skip
    assert result.bindings["d"] == Series((2.0, 1.0, 1.0, 6.0))
    assert result.bindings["m"] == Series((2.5, 4.0, 2.5, 9.0))
    assert result.bindings["z"] == Series((-1.0, 1.0, 4.0, 2.0))
    assert result.bindings["f"] == 2.0
    assert result.signals["ok"] == Series((False, True, False, True))
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        execute(lower_program(parse(src)), bar_count=4, inputs={"close": close})
