"""BT-15a (2/2) — numpy vectorization of DSL `Series` operations (DSL-8 `runtime/series.py`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(1/2). Prerequisite: DSL-8 `runtime/series.py`(9fa72b5).

This module re-implements, as numpy arrays, the elementwise operations
(arithmetic, comparison, cross, three-valued logic, shift, nz/na) that
`runtime/series.py` defines with plain Python tuples and loops. It does not
invent new signal semantics — it reproduces the same semantics under a
different execution strategy (whole-array numpy vector operations instead of
a Python function call per element). The two implementations must produce
the same value per bar, and that equivalence ("identical event path and
elements") is what this leaf's tests check against, treating `runtime.series`
as ground truth.

na representation differs across the two value domains:
- For numeric values (`FloatArray`, float64), we follow the same convention
  as `engine/vectorized.py` (IND-1) — `NaN` itself is na. Since
  `Series.of_floats` already rejects non-finite elements, there is no way
  for "genuine NaN data" and "na" to get mixed up.
- bool has no numpy dtype that can express na, so it is represented as a
  `BoolSignal` carrying `values` (dtype=bool, with a meaningless placeholder
  `False` at na positions) alongside an `na` (dtype=bool) mask. A masked
  array would add too much overhead for operations this small, and an
  object array would defeat vectorization from the outset, so neither is
  used.

Scope: this leaf covers only the float domain. The DSL's int domain
(`runtime.series.arith(..., integer=True)`, truncating division toward
zero) only matters for series whose entire array is integer, and indicator
and price signals are mostly in the float domain, so it isn't needed right
now — if it's ever needed, it will be handled explicitly in a new leaf
(not built ahead of need on speculation).

Pure module — no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.core.script.runtime.series import ArithOp, CompareOp, CrossOp, LogicalOp, Series

__all__ = [
    "ArithOp",
    "BoolSignal",
    "FloatArray",
    "VectorSignalError",
    "arith",
    "bool_from_series",
    "bool_to_series",
    "compare",
    "cross",
    "is_na",
    "logical",
    "logical_not",
    "numeric_from_series",
    "numeric_to_series",
    "nz",
    "shift_bool",
    "shift_numeric",
]

FloatArray = np.ndarray[Any, np.dtype[np.float64]]
BoolArray = np.ndarray[Any, np.dtype[np.bool_]]

class VectorSignalError(ValueError):
    """`BT_VECTOR_SIGNAL` — fail-closed rejection for array length mismatches, negative
    shift offsets, and the like."""


@dataclass(frozen=True, slots=True)
class BoolSignal:
    """Vector representation of a bool series. The numpy counterpart of `Series.of_bools`.
    At positions where `na=True`, `values` holds an undefined placeholder (`False`) and
    must not be read."""

    values: BoolArray
    na: BoolArray

    def __post_init__(self) -> None:
        if self.values.shape != self.na.shape:
            raise VectorSignalError(
                f"BoolSignal.values shape {self.values.shape} != na shape {self.na.shape}"
            )
        if self.values.dtype != np.bool_:
            raise VectorSignalError(f"BoolSignal.values dtype이 bool이 아니다: {self.values.dtype}")
        if self.na.dtype != np.bool_:
            raise VectorSignalError(f"BoolSignal.na dtype이 bool이 아니다: {self.na.dtype}")

    def __len__(self) -> int:
        return len(self.values)


def _same_length(left: Any, right: Any) -> None:
    if len(left) != len(right):
        raise VectorSignalError(f"시리즈 길이 불일치: {len(left)} != {len(right)}")


# ---- DSL Series <-> vector representation round-trip (for tests / future BT-15b bridge) ----


def numeric_from_series(series: Series) -> FloatArray:
    """`series<float>` -> `FloatArray`(na=`None` -> `NaN`)."""
    return np.array(
        [np.nan if v is None else float(v) for v in series.values], dtype=np.float64
    )


def numeric_to_series(values: FloatArray) -> Series:
    """`FloatArray` -> `series<float>`(`NaN` -> `None`)."""
    return Series(tuple(None if math.isnan(v) else float(v) for v in values))


def bool_from_series(series: Series) -> BoolSignal:
    """`series<bool>` -> `BoolSignal`."""
    na = np.array([v is None for v in series.values], dtype=np.bool_)
    values = np.array([False if v is None else v for v in series.values], dtype=np.bool_)
    return BoolSignal(values=values, na=na)


def bool_to_series(signal: BoolSignal) -> Series:
    """`BoolSignal` -> `series<bool>`."""
    return Series(
        tuple(None if na else bool(v) for v, na in zip(signal.values, signal.na, strict=True))
    )


# ---- Arithmetic / comparison / cross ----


def arith(op: ArithOp, left: FloatArray, right: FloatArray) -> FloatArray:
    """Elementwise equivalent of `runtime.series.arith(op, left, right, integer=False)`.
    `NaN` already produces the same result as na propagation under IEEE754
    propagation rules, and division-by-zero / non-finite results are filtered
    with `np.isfinite` and collapsed to `NaN` (na) (the same rule as
    `_float_arith`'s `_finite`)."""
    _same_length(left, right)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if op == "+":
            raw = left + right
        elif op == "-":
            raw = left - right
        elif op == "*":
            raw = left * right
        elif op == "/":
            raw = left / right
        else:
            raise VectorSignalError(f"알 수 없는 산술 연산자: {op!r}")
    return np.where(np.isfinite(raw), raw, np.nan)


def compare(op: CompareOp, left: FloatArray, right: FloatArray) -> BoolSignal:
    """Elementwise equivalent of `runtime.series.compare`. If either operand is
    `NaN` (na), the result is also na (numpy's comparison rule `nan < x = False`
    means "false", not na, so this is handled explicitly here to keep the
    distinction)."""
    _same_length(left, right)
    na = np.isnan(left) | np.isnan(right)
    with np.errstate(invalid="ignore"):
        if op == "<":
            raw = left < right
        elif op == "<=":
            raw = left <= right
        elif op == "==":
            raw = left == right
        elif op == ">=":
            raw = left >= right
        elif op == ">":
            raw = left > right
        else:
            raise VectorSignalError(f"알 수 없는 비교 연산자: {op!r}")
    return BoolSignal(values=np.where(na, False, raw), na=na)


def cross(op: CrossOp, left: FloatArray, right: FloatArray) -> BoolSignal:
    """Elementwise equivalent of `runtime.series.cross`. Unlike
    `runtime.series.cross`, this does not support scalar broadcasting — this
    module only deals with arrays already materialized to the bar count (see
    the module docstring's scope)."""
    _same_length(left, right)
    n = len(left)
    if n == 0:
        return BoolSignal(values=np.zeros(0, dtype=np.bool_), na=np.zeros(0, dtype=np.bool_))
    prev_left = np.empty(n, dtype=np.float64)
    prev_left[0] = np.nan
    prev_left[1:] = left[:-1]
    prev_right = np.empty(n, dtype=np.float64)
    prev_right[0] = np.nan
    prev_right[1:] = right[:-1]
    na = np.isnan(left) | np.isnan(right) | np.isnan(prev_left) | np.isnan(prev_right)
    with np.errstate(invalid="ignore"):
        if op == "crosses_above":
            raw = (left > right) & (prev_left <= prev_right)
        elif op == "crosses_below":
            raw = (left < right) & (prev_left >= prev_right)
        else:
            raise VectorSignalError(f"알 수 없는 교차 연산자: {op!r}")
    return BoolSignal(values=np.where(na, False, raw), na=na)


# ---- Logic (three-valued Kleene) ----


def logical(op: LogicalOp, left: BoolSignal, right: BoolSignal) -> BoolSignal:
    """Elementwise equivalent of `runtime.series.logical` (three-valued Kleene
    logic): for `and`, if either side is definitely `False`, the result is
    `False` regardless of na status; otherwise, if either side is na, the
    result is na; if both are definitely `True`, the result is `True`. `or` is
    symmetric (if either side is definitely `True`, the result is `True`)."""
    _same_length(left, right)
    ln, rn = left.na, right.na
    lv, rv = left.values, right.values
    if op == "and":
        decided = (~ln & ~lv) | (~rn & ~rv)  # either side is definitely False
        na = ~decided & (ln | rn)
        values = ~decided & ~na
    elif op == "or":
        decided = (~ln & lv) | (~rn & rv)  # either side is definitely True
        na = ~decided & (ln | rn)
        values = decided
    else:
        raise VectorSignalError(f"알 수 없는 논리 연산자: {op!r}")
    return BoolSignal(values=values, na=na)


def logical_not(value: BoolSignal) -> BoolSignal:
    return BoolSignal(values=np.where(value.na, False, ~value.values), na=value.na.copy())


# ---- Shift (indexing) ----


def shift_numeric(value: FloatArray, offset: int) -> FloatArray:
    """Elementwise equivalent of `s[offset]`: the value at bar t equals
    `value[t-offset]`, or na (`NaN`) when `t<offset`."""
    if offset < 0:
        raise VectorSignalError(f"시리즈 오프셋은 0 이상이어야 합니다: {offset}")
    n = len(value)
    k = min(offset, n)
    out = np.empty(n, dtype=np.float64)
    out[:k] = np.nan
    out[k:] = value[: n - k]
    return out


def shift_bool(value: BoolSignal, offset: int) -> BoolSignal:
    if offset < 0:
        raise VectorSignalError(f"시리즈 오프셋은 0 이상이어야 합니다: {offset}")
    n = len(value)
    k = min(offset, n)
    values = np.empty(n, dtype=np.bool_)
    na = np.empty(n, dtype=np.bool_)
    values[:k] = False
    na[:k] = True
    values[k:] = value.values[: n - k]
    na[k:] = value.na[: n - k]
    return BoolSignal(values=values, na=na)


# ---- nz/na ----


def nz(value: FloatArray, fill: float = 0.0) -> FloatArray:
    return np.where(np.isnan(value), fill, value)


def is_na(value: FloatArray) -> BoolSignal:
    """Whether a numeric series element is na. The result itself is never na
    (as defined in the `runtime.series` module docstring)."""
    mask = np.isnan(value)
    return BoolSignal(values=mask, na=np.zeros(len(value), dtype=np.bool_))
