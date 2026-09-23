"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 table row 88 / §9.4 DSL-8 —
AIOS Script runtime value model: `Series` and element-wise operations (broadcast).

A value has one of two shapes. `Scalar` (int/float/bool, a value independent of any bar)
and `Series` (one per bar, length = bar count). `None` is na (missing) — like Pine's `na`,
it means "no value yet" and is represented with the single sentinel `None` rather than a
separate marker. Series elements may also be `None`.

§3.3 semantics (this leaf fixes the v1 rules — anything not in the spec's grammar table is
pinned here, and the reference implementation's property tests independently reimplement
the same definitions to cross-check):
- Indexing `s[n]` (`shift`): the value at bar t = s's value at bar t-n, na if t<n. Only the
  past is visible (negative n is already rejected by the grammar/DSL-5; here too, below 0
  is an error).
- na propagation: arithmetic, comparison, sign negation, and cross all yield na if either
  operand is na.
- Logic (and/or/not) is three-valued (Kleene): `False and na = False`, `True or na = True`;
  otherwise na mixed in yields na. Consumers must treat only `True` as a fire (fail-closed —
  na means "unknown", not "no").
- Division by zero and non-finite results (inf/nan) yield na instead of an exception. Integer
  `/` truncates toward zero (static int type; matches DSL-4 `promote_numeric`'s int/int→int).
- `crosses_above(a, b)`: at bar t, `a[t] > b[t] and a[t-1] <= b[t-1]`; na if t=0 or any of
  the four values is na. `crosses_below` reverses the inequality. A cross is bar-dimensioned,
  so the result is always a length-`bar_count` series even if both operands are scalars.
- nz/na: `nz(x, fill=0)` replaces na with `fill`, and `is_na(x)` is a bool series of na-ness.
  Both are series-only ops; which name (`math.nz`, etc.) exposes them is DSL-9's concern.
- The integer domain rejects bool (explicit check since Python `bool` is a subtype of `int`).

Pure, no I/O, no recursion. Operations between series of different lengths raise
`ScriptRuntimeError` (fail-closed — never silently truncate or pad).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Literal

Scalar = int | float | bool | None
"""A value independent of any bar. `None` = na."""

ArithOp = Literal["+", "-", "*", "/"]
CompareOp = Literal["<", "<=", "==", ">=", ">"]
LogicalOp = Literal["and", "or"]
CrossOp = Literal["crosses_above", "crosses_below"]


class ScriptRuntimeError(Exception):
    """IR execution failure (shape/domain mismatch, length mismatch, unregistered builtin,
    unbound name).

    §3.3's taxonomy defines only the 4 compile-error kinds. A runtime error means "IR that
    passed checks doesn't match the input/registry the host supplied", so it gets its own
    code and is always raised as an exception (no silent default).
    """

    code: Final = "SCRIPT_RUNTIME"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Series:
    """Bar-aligned series. Elements are float/bool/None (na). Immutable."""

    values: tuple[Scalar, ...]

    @classmethod
    def of_floats(cls, values: Iterable[float | int | None]) -> Series:
        """Numeric-element series. Promotes int to float; rejects bool and non-finite numbers."""
        out: list[Scalar] = []
        for i, v in enumerate(values):
            if v is None:
                out.append(None)
            elif isinstance(v, bool) or not isinstance(v, int | float):
                raise ScriptRuntimeError(f"series<float> 원소 #{i}가 수치가 아닙니다: {v!r}")
            elif not math.isfinite(v):
                raise ScriptRuntimeError(f"series<float> 원소 #{i}가 유한수가 아닙니다: {v!r}")
            else:
                out.append(float(v))
        return cls(tuple(out))

    @classmethod
    def of_bools(cls, values: Iterable[bool | None]) -> Series:
        out: list[Scalar] = []
        for i, v in enumerate(values):
            if v is not None and not isinstance(v, bool):
                raise ScriptRuntimeError(f"series<bool> 원소 #{i}가 bool이 아닙니다: {v!r}")
            out.append(v)
        return cls(tuple(out))

    def __len__(self) -> int:
        return len(self.values)

    def at(self, bar: int) -> Scalar:
        """Value at bar `bar` (>= 0). Errors out of range (never silently becomes na)."""
        if not 0 <= bar < len(self.values):
            raise ScriptRuntimeError(f"봉 인덱스 범위 밖: {bar} (길이 {len(self.values)})")
        return self.values[bar]

    def shift(self, offset: int) -> Series:
        """`s[offset]` — bar t's value becomes s[t-offset]; the leading `offset` bars are na."""
        if offset < 0:
            raise ScriptRuntimeError(f"시리즈 오프셋은 0 이상이어야 합니다: {offset}")
        n = len(self.values)
        k = min(offset, n)
        return Series((None,) * k + self.values[: n - k])

    def is_na(self) -> Series:
        return Series(tuple(v is None for v in self.values))

    def nz(self, fill: float | int | bool = 0.0) -> Series:
        return Series(tuple(fill if v is None else v for v in self.values))

    def map(self, fn: Callable[[Scalar], Scalar]) -> Series:
        return Series(tuple(fn(v) for v in self.values))


Value = Scalar | Series
"""Interpreter stack value: scalar or series."""


def broadcast(value: Value, bar_count: int) -> Series:
    """Expand a scalar into a length-`bar_count` series; if already a series, only check length."""
    if isinstance(value, Series):
        if len(value) != bar_count:
            raise ScriptRuntimeError(f"시리즈 길이 불일치: {len(value)} != 봉 수 {bar_count}")
        return value
    return Series((value,) * bar_count)


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


def _int_div(a: int, b: int) -> int:
    """Integer division truncated toward zero (exact, no float detour). Assumes b != 0."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _number(v: Scalar, integer: bool) -> int | float | None:
    """Validate the numeric domain and return as-is (None is na). Rejects bool and non-numeric."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ScriptRuntimeError(f"수치 연산 피연산자가 수치가 아닙니다: {v!r}")
    if integer and not isinstance(v, int):
        raise ScriptRuntimeError(f"int 연산 피연산자가 int가 아닙니다: {v!r}")
    return v


def _boolean(v: Scalar) -> bool | None:
    if v is not None and not isinstance(v, bool):
        raise ScriptRuntimeError(f"논리 연산 피연산자가 bool이 아닙니다: {v!r}")
    return v


def _zip(left: Value, right: Value, kernel: Callable[[Scalar, Scalar], Scalar]) -> Value:
    """Broadcast an element-wise binary kernel over a scalar/series combination."""
    if isinstance(left, Series):
        if not isinstance(right, Series):
            return Series(tuple(kernel(a, right) for a in left.values))
        if len(left) != len(right):
            raise ScriptRuntimeError(f"시리즈 길이 불일치: {len(left)} != {len(right)}")
        return Series(tuple(kernel(a, b) for a, b in zip(left.values, right.values, strict=True)))
    if isinstance(right, Series):
        return Series(tuple(kernel(left, b) for b in right.values))
    return kernel(left, right)


def _map(value: Value, kernel: Callable[[Scalar], Scalar]) -> Value:
    return value.map(kernel) if isinstance(value, Series) else kernel(value)


# ---- Arithmetic ----


def arith(op: ArithOp, left: Value, right: Value, *, integer: bool) -> Value:
    """+ - * /. `integer=True` means the int domain (DSL-4 result type int); otherwise float."""

    def kernel(a: Scalar, b: Scalar) -> Scalar:
        x, y = _number(a, integer), _number(b, integer)
        if x is None or y is None:
            return None
        if integer:
            return _int_arith(op, int(x), int(y))
        return _float_arith(op, float(x), float(y))

    return _zip(left, right, kernel)


def _int_arith(op: ArithOp, a: int, b: int) -> int | None:
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    return None if b == 0 else _int_div(a, b)


def _float_arith(op: ArithOp, a: float, b: float) -> float | None:
    if op == "+":
        return _finite(a + b)
    if op == "-":
        return _finite(a - b)
    if op == "*":
        return _finite(a * b)
    return None if b == 0.0 else _finite(a / b)


def negate(value: Value, *, integer: bool) -> Value:
    def kernel(a: Scalar) -> Scalar:
        x = _number(a, integer)
        if x is None:
            return None
        return -int(x) if integer else _finite(-float(x))

    return _map(value, kernel)


# ---- Comparison / cross ----


def compare(op: CompareOp, left: Value, right: Value) -> Value:
    def kernel(a: Scalar, b: Scalar) -> Scalar:
        x, y = _number(a, False), _number(b, False)
        if x is None or y is None:
            return None
        return _compare_scalars(op, x, y)

    return _zip(left, right, kernel)


def _compare_scalars(op: CompareOp, a: float, b: float) -> bool:
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == "==":
        return a == b
    if op == ">=":
        return a >= b
    return a > b


def cross(op: CrossOp, left: Value, right: Value, *, bar_count: int) -> Series:
    """Cross, defined in the module docstring. Result is always a series (adds bar dimension)."""
    lv, rv = broadcast(left, bar_count).values, broadcast(right, bar_count).values
    out: list[Scalar] = []
    for t in range(bar_count):
        a, b = _number(lv[t], False), _number(rv[t], False)
        pa, pb = (lv[t - 1], rv[t - 1]) if t > 0 else (None, None)
        if a is None or b is None or pa is None or pb is None:
            out.append(None)
        elif op == "crosses_above":
            out.append(a > b and pa <= pb)
        else:
            out.append(a < b and pa >= pb)
    return Series(tuple(out))


# ---- Logic (three-valued) ----


def logical(op: LogicalOp, left: Value, right: Value) -> Value:
    def kernel(a: Scalar, b: Scalar) -> Scalar:
        x, y = _boolean(a), _boolean(b)
        if op == "and":
            if x is False or y is False:
                return False
            return None if x is None or y is None else True
        if x is True or y is True:
            return True
        return None if x is None or y is None else False

    return _zip(left, right, kernel)


def logical_not(value: Value) -> Value:
    def kernel(a: Scalar) -> Scalar:
        x = _boolean(a)
        return None if x is None else not x

    return _map(value, kernel)


# ---- Indexing ----


def index(value: Value, offset: int) -> Series:
    """`[offset]`. The static type guarantees a series, so a scalar here is an IR/input mismatch."""
    if not isinstance(value, Series):
        raise ScriptRuntimeError(f"'[n]' 인덱싱 대상이 시리즈가 아닙니다: {value!r}")
    return value.shift(offset)
