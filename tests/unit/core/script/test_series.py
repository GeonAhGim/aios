"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 — `runtime/series.py` 테스트.

모듈 docstring이 고정한 §3.3 v1 의미론(인덱싱·na 전파·3치 논리·0 나눗셈·정수
절삭·교차·nz)을 값 단위로 단언하고, negative(길이 불일치·도메인 위반·음수
오프셋·범위 밖 접근·비유한 입력)가 전부 `ScriptRuntimeError`인지 확인한다.
"""
from __future__ import annotations

import math

import pytest

from src.core.script.runtime import (
    ScriptRuntimeError,
    Series,
    arith,
    broadcast,
    compare,
    cross,
    index,
    logical,
    logical_not,
    negate,
)


def _f(*values: float | int | None) -> Series:
    return Series.of_floats(values)


# ---- 인덱싱 ----


def test_shift_moves_values_forward_and_fills_na() -> None:
    s = _f(1, 2, 3, 4)
    assert s.shift(0) == s
    assert s.shift(1).values == (None, 1.0, 2.0, 3.0)
    assert s.shift(3).values == (None, None, None, 1.0)
    assert s.shift(10).values == (None, None, None, None)
    assert index(s, 2).values == (None, None, 1.0, 2.0)


def test_shift_rejects_negative_offset_and_index_rejects_scalar() -> None:
    with pytest.raises(ScriptRuntimeError):
        _f(1.0).shift(-1)
    with pytest.raises(ScriptRuntimeError):
        index(3.0, 1)


def test_at_rejects_out_of_range_instead_of_returning_na() -> None:
    s = _f(1, 2)
    assert s.at(1) == 2.0
    with pytest.raises(ScriptRuntimeError):
        s.at(2)
    with pytest.raises(ScriptRuntimeError):
        s.at(-1)


# ---- 산술·na·0 나눗셈 ----


def test_arith_broadcasts_scalar_and_propagates_na() -> None:
    assert arith("+", _f(1, None, 3), 1.0, integer=False).values == (2.0, None, 4.0)
    assert arith("*", 2, _f(1, 2), integer=False).values == (2.0, 4.0)
    assert arith("-", _f(5, 5), _f(1, None), integer=False).values == (4.0, None)
    assert arith("+", 1, 2, integer=True) == 3
    assert arith("+", None, 2, integer=True) is None


def test_division_by_zero_and_non_finite_results_become_na() -> None:
    assert arith("/", 1.0, 0.0, integer=False) is None
    assert arith("/", 7, 0, integer=True) is None
    assert arith("*", 1e308, 10.0, integer=False) is None
    assert arith("/", _f(1, 0), _f(0, 0), integer=False).values == (None, None)


def test_integer_division_truncates_toward_zero() -> None:
    assert arith("/", 7, 2, integer=True) == 3
    assert arith("/", -7, 2, integer=True) == -3
    assert arith("/", 7, -2, integer=True) == -3
    assert arith("/", -7, -2, integer=True) == 3
    assert arith("/", 7.0, 2.0, integer=False) == 3.5


def test_integer_domain_rejects_float_and_bool_operands() -> None:
    with pytest.raises(ScriptRuntimeError):
        arith("+", 1.5, 2, integer=True)
    with pytest.raises(ScriptRuntimeError):
        arith("+", True, 2, integer=True)
    with pytest.raises(ScriptRuntimeError):
        arith("+", True, 2.0, integer=False)
    with pytest.raises(ScriptRuntimeError):
        negate(True, integer=False)


def test_negate_keeps_int_domain_and_propagates_na() -> None:
    assert negate(3, integer=True) == -3
    assert negate(_f(1, None), integer=False).values == (-1.0, None)


def test_series_length_mismatch_is_rejected() -> None:
    with pytest.raises(ScriptRuntimeError):
        arith("+", _f(1, 2), _f(1), integer=False)
    with pytest.raises(ScriptRuntimeError):
        broadcast(_f(1, 2), 3)
    assert broadcast(2.0, 3).values == (2.0, 2.0, 2.0)


# ---- 비교·교차 ----


def test_compare_yields_bool_or_na() -> None:
    assert compare("<", _f(1, None, 3), 2).values == (True, None, False)
    assert compare("==", 2, 2.0) is True
    assert compare(">=", None, 1) is None
    with pytest.raises(ScriptRuntimeError):
        compare("<", True, 1)


def test_cross_definition_with_na_at_bar_zero() -> None:
    a = _f(1, 3, 2, 5, 5)
    above = cross("crosses_above", a, 2.5, bar_count=5)
    assert above.values == (None, True, False, True, False)
    below = cross("crosses_below", a, 2.5, bar_count=5)
    assert below.values == (None, False, True, False, False)


def test_cross_of_two_scalars_is_series_never_true() -> None:
    assert cross("crosses_above", 1, 2, bar_count=3).values == (None, False, False)
    with pytest.raises(ScriptRuntimeError):
        cross("crosses_above", _f(1, 2), 3, bar_count=3)


def test_cross_propagates_na_from_previous_bar() -> None:
    assert cross("crosses_above", _f(1, None, 5), 2, bar_count=3).values == (None, None, None)


# ---- 3치 논리 ----


def test_kleene_and_or_not() -> None:
    assert logical("and", False, None) is False
    assert logical("and", True, None) is None
    assert logical("and", True, True) is True
    assert logical("or", True, None) is True
    assert logical("or", False, None) is None
    assert logical("or", False, False) is False
    assert logical_not(None) is None
    assert logical_not(Series.of_bools([True, None])).values == (False, None)
    with pytest.raises(ScriptRuntimeError):
        logical("and", 1, True)
    with pytest.raises(ScriptRuntimeError):
        logical_not(0)


# ---- nz / na / 생성자 ----


def test_nz_and_is_na() -> None:
    s = _f(1, None)
    assert s.is_na().values == (False, True)
    assert s.nz().values == (1.0, 0.0)
    assert s.nz(-1).values == (1.0, -1)


def test_constructors_reject_wrong_domain_and_non_finite() -> None:
    with pytest.raises(ScriptRuntimeError):
        Series.of_floats([True])
    with pytest.raises(ScriptRuntimeError):
        Series.of_floats([math.inf])
    with pytest.raises(ScriptRuntimeError):
        Series.of_floats(["1"])  # type: ignore[list-item]
    with pytest.raises(ScriptRuntimeError):
        Series.of_bools([1])  # type: ignore[list-item]
    assert Series.of_floats([1, 2.5, None]).values == (1.0, 2.5, None)
    assert Series.of_bools([True, None]).values == (True, None)
