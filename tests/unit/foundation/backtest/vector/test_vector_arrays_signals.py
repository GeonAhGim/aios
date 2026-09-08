"""BT-15a — `backtest/vector/{arrays,signals}.py` 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(1/2). DoD: (a) `arrays.from_candle_columns`가 `CandleColumns`의 필드
이름·순서·dtype과 일치하는 numpy 배열을 만드는지(§C 중복 컨텍스트를 만들지
않았는지) 단언한다. (b) `signals`의 각 연산이 DSL-8
`runtime/series.py`(이벤트 경로가 실제로 쓰는 스칼라 구현)와 원소 단위로
동일한 값을 내는지, `tests/unit/core/script/test_series.py`가 이미 진실로
검증한 것과 같은 입력·기댓값을 재사용해 대조한다.
"""
from __future__ import annotations

import math
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.core.script.runtime import series as scalar
from src.core.script.runtime.series import LogicalOp
from src.foundation.backtest.vector import arrays, signals
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

_D = Decimal
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _columns(n: int) -> CandleColumns:
    closes = [_D(str(100 + i)) for i in range(n)]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(n)],
        open=[c - 1 for c in closes],
        high=[c + 1 for c in closes],
        low=[c - 2 for c in closes],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None if i % 2 == 0 else _D("5000") for i in range(n)],
    )


def _assert_float_array_equal(actual: np.ndarray, expected: list[float | None]) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected, strict=True):
        if e is None:
            assert math.isnan(a), f"expected na(NaN), got {a}"
        else:
            assert a == pytest.approx(e), f"expected {e}, got {a}"


def _f(*values: float | int | None) -> np.ndarray:
    return signals.numeric_from_series(scalar.Series.of_floats(values))


def _b(*values: bool | None) -> signals.BoolSignal:
    return signals.bool_from_series(scalar.Series.of_bools(values))


# ==== arrays.py ====


def test_from_candle_columns_field_order_and_dtypes_match_candle_columns() -> None:
    columns = _columns(3)
    out = arrays.from_candle_columns(columns)

    columns_field_names = [f.name for f in fields(CandleColumns)]
    arrays_field_names = [f.name for f in fields(arrays.CandleArrays)]
    assert arrays_field_names == columns_field_names == [
        "ts", "open", "high", "low", "close", "volume", "quote_volume",
    ]
    assert out.ts.dtype == np.int64
    for name in ("open", "high", "low", "close", "volume", "quote_volume"):
        assert getattr(out, name).dtype == np.float64
    assert len(out) == 3


def test_from_candle_columns_converts_values_and_none_quote_volume_to_nan() -> None:
    columns = _columns(2)
    out = arrays.from_candle_columns(columns)

    assert out.close.tolist() == [100.0, 101.0]
    assert out.open.tolist() == [99.0, 100.0]
    assert math.isnan(out.quote_volume[0])
    assert out.quote_volume[1] == 5000.0


def test_from_candle_columns_epoch_ns_is_exact_and_monotonic() -> None:
    columns = _columns(3)
    out = arrays.from_candle_columns(columns)

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    expected0 = int((_T0 - epoch).total_seconds()) * 1_000_000_000
    assert out.ts[0] == expected0
    assert out.ts[1] - out.ts[0] == 60_000_000_000  # 1분 = 60e9 ns
    assert out.ts[2] - out.ts[1] == 60_000_000_000


def test_from_candle_columns_rejects_mismatched_lengths() -> None:
    columns = _columns(3)
    bad = CandleColumns(
        ts=columns.ts, open=columns.open, high=columns.high, low=columns.low,
        close=columns.close[:-1], volume=columns.volume, quote_volume=columns.quote_volume,
    )
    with pytest.raises(MismatchedColumnLengthError):
        arrays.from_candle_columns(bad)


def test_candle_arrays_rejects_wrong_dtype_directly() -> None:
    n = 2
    ok = np.zeros(n, dtype=np.float64)
    with pytest.raises(arrays.ArrayDtypeError):
        arrays.CandleArrays(
            ts=np.zeros(n, dtype=np.int32),  # 잘못된 dtype
            open=ok, high=ok, low=ok, close=ok, volume=ok, quote_volume=ok,
        )
    with pytest.raises(arrays.ArrayDtypeError):
        arrays.CandleArrays(
            ts=np.zeros(n, dtype=np.int64),
            open=np.zeros(n, dtype=np.int64),  # 잘못된 dtype
            high=ok, low=ok, close=ok, volume=ok, quote_volume=ok,
        )


def test_candle_arrays_rejects_length_mismatch_directly() -> None:
    ok2 = np.zeros(2, dtype=np.float64)
    ok3 = np.zeros(3, dtype=np.float64)
    with pytest.raises(MismatchedColumnLengthError):
        arrays.CandleArrays(
            ts=np.zeros(2, dtype=np.int64),
            open=ok2, high=ok2, low=ok2, close=ok3, volume=ok2, quote_volume=ok2,
        )


# ==== signals.py — 이벤트 경로(runtime.series)와 원소 단위 동등성 ====


def test_arith_matches_runtime_series_element_wise() -> None:
    left, right = _f(1, None, 3), _f(1, 1, 1)
    left_s, right_s = scalar.Series.of_floats((1, None, 3)), scalar.Series.of_floats((1, 1, 1))
    expected = scalar.arith("+", left_s, right_s, integer=False)
    _assert_float_array_equal(signals.arith("+", left, right), list(expected.values))

    a, b = _f(5, 5), _f(1, None)
    a_s, b_s = scalar.Series.of_floats((5, 5)), scalar.Series.of_floats((1, None))
    expected2 = scalar.arith("-", a_s, b_s, integer=False)
    _assert_float_array_equal(signals.arith("-", a, b), list(expected2.values))


def test_division_by_zero_and_non_finite_results_become_na() -> None:
    _assert_float_array_equal(signals.arith("/", _f(1, 0), _f(0, 0)), [None, None])
    _assert_float_array_equal(signals.arith("*", _f(1e308), _f(10.0)), [None])


def test_compare_matches_runtime_series() -> None:
    left, right = _f(1, None, 3), _f(2, 2, 2)
    left_s, right_s = scalar.Series.of_floats((1, None, 3)), scalar.Series.of_floats((2, 2, 2))
    expected = scalar.compare("<", left_s, right_s)
    result = signals.compare("<", left, right)
    for value, na, exp in zip(result.values, result.na, expected.values, strict=True):
        if exp is None:
            assert na
        else:
            assert not na
            assert bool(value) == exp


def test_cross_matches_runtime_series_with_na_at_bar_zero() -> None:
    a = _f(1, 3, 2, 5, 5)
    b = _f(2.5, 2.5, 2.5, 2.5, 2.5)
    expected_above = scalar.cross(
        "crosses_above", scalar.Series.of_floats((1, 3, 2, 5, 5)), 2.5, bar_count=5
    )
    above = signals.cross("crosses_above", a, b)
    for value, na, exp in zip(above.values, above.na, expected_above.values, strict=True):
        assert na == (exp is None)
        if exp is not None:
            assert bool(value) == exp

    expected_below = scalar.cross(
        "crosses_below", scalar.Series.of_floats((1, 3, 2, 5, 5)), 2.5, bar_count=5
    )
    below = signals.cross("crosses_below", a, b)
    for value, na, exp in zip(below.values, below.na, expected_below.values, strict=True):
        assert na == (exp is None)
        if exp is not None:
            assert bool(value) == exp


def test_cross_propagates_na_from_previous_bar() -> None:
    a = _f(1, None, 5)
    b = _f(2, 2, 2)
    out = signals.cross("crosses_above", a, b)
    assert out.na.tolist() == [True, True, True]


def test_kleene_and_or_matches_runtime_series() -> None:
    cases: list[tuple[LogicalOp, bool | None, bool | None, bool | None]] = [
        ("and", False, None, False),
        ("and", True, None, None),
        ("and", True, True, True),
        ("or", True, None, True),
        ("or", False, None, None),
        ("or", False, False, False),
    ]
    for op, lv, rv, expected in cases:
        assert scalar.logical(op, lv, rv) == expected
        left = _b(lv)
        right = _b(rv)
        out = signals.logical(op, left, right)
        if expected is None:
            assert out.na[0]
        else:
            assert not out.na[0]
            assert bool(out.values[0]) == expected


def test_logical_not_matches_runtime_series() -> None:
    expected = scalar.logical_not(scalar.Series.of_bools([True, None, False]))
    out = signals.logical_not(_b(True, None, False))
    for value, na, exp in zip(out.values, out.na, expected.values, strict=True):
        assert na == (exp is None)
        if exp is not None:
            assert bool(value) == exp


def test_shift_matches_runtime_series() -> None:
    s = scalar.Series.of_floats((1, 2, 3, 4))
    arr = _f(1, 2, 3, 4)
    for offset in (0, 1, 3, 10):
        expected = s.shift(offset)
        _assert_float_array_equal(signals.shift_numeric(arr, offset), list(expected.values))


def test_shift_rejects_negative_offset() -> None:
    with pytest.raises(signals.VectorSignalError):
        signals.shift_numeric(_f(1.0), -1)
    with pytest.raises(signals.VectorSignalError):
        signals.shift_bool(_b(True), -1)


def test_shift_bool_matches_runtime_series() -> None:
    s = scalar.Series.of_bools([True, False, True, None])
    sig = _b(True, False, True, None)
    for offset in (0, 2, 5):
        expected = s.shift(offset)
        out = signals.shift_bool(sig, offset)
        for value, na, exp in zip(out.values, out.na, expected.values, strict=True):
            assert na == (exp is None)
            if exp is not None:
                assert bool(value) == exp


def test_nz_and_is_na_match_runtime_series() -> None:
    s = scalar.Series.of_floats((1, None))
    arr = _f(1, None)
    assert signals.is_na(arr).values.tolist() == list(s.is_na().values)
    _assert_float_array_equal(signals.nz(arr), list(s.nz().values))
    _assert_float_array_equal(signals.nz(arr, -1), list(s.nz(-1).values))


def test_numeric_series_round_trip() -> None:
    s = scalar.Series.of_floats((1, 2.5, None))
    assert signals.numeric_to_series(signals.numeric_from_series(s)) == s


def test_bool_series_round_trip() -> None:
    s = scalar.Series.of_bools([True, None, False])
    assert signals.bool_to_series(signals.bool_from_series(s)) == s


def test_length_mismatch_is_rejected() -> None:
    with pytest.raises(signals.VectorSignalError):
        signals.arith("+", _f(1, 2), _f(1))
    with pytest.raises(signals.VectorSignalError):
        signals.compare("<", _f(1, 2), _f(1))
    with pytest.raises(signals.VectorSignalError):
        signals.cross("crosses_above", _f(1, 2), _f(1))
    with pytest.raises(signals.VectorSignalError):
        signals.logical("and", _b(True, False), _b(True))


def test_bool_signal_rejects_shape_and_dtype_mismatch() -> None:
    with pytest.raises(signals.VectorSignalError):
        signals.BoolSignal(values=np.zeros(2, dtype=np.bool_), na=np.zeros(3, dtype=np.bool_))
    with pytest.raises(signals.VectorSignalError):
        signals.BoolSignal(values=np.zeros(2, dtype=np.float64), na=np.zeros(2, dtype=np.bool_))
