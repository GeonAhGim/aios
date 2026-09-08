"""BT-15a (2/2) — DSL `Series` 연산(DSL-8 `runtime/series.py`)의 numpy 벡터화.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(1/2). 선행: DSL-8 `runtime/series.py`(9fa72b5).

이 모듈은 `runtime/series.py`가 파이썬 튜플·루프로 정의한 원소별 연산(산술·
비교·교차·3치 논리·시프트·nz/na)을 numpy 배열로 다시 구현한다. 새 신호
의미론을 만드는 게 아니라 같은 의미론을 다른 실행 전략(원소마다 파이썬
함수 호출 대신 배열 전체에 numpy 벡터 연산)으로 재현한다 — 두 구현은 봉마다
같은 값을 내야 하고, 그 동등성("이벤트 경로와 원소 동일")이 이 리프의
테스트가 `runtime.series`를 진실로 삼아 대조하는 대상이다.

na 표현은 두 값 도메인에서 다르다:
- 수치(`FloatArray`, float64)는 `engine/vectorized.py`(IND-1)와 같은 관례를
  따른다 — `NaN`이 곧 na다. `Series.of_floats`가 애초에 비유한수 원소를
  거부하므로 "진짜 NaN 데이터"와 "na"가 섞일 일이 없다.
- bool은 numpy에 na를 표현할 dtype이 없어 `values`(dtype=bool, na 위치는
  의미 없는 자리값 `False`) 옆에 `na`(dtype=bool) 마스크를 나란히 든
  `BoolSignal`로 표현한다. masked array는 이 정도 연산에 오버헤드가 크고,
  object 배열은 애초에 벡터화를 무효화하므로 채택하지 않는다.

스코프: 이 리프는 float 도메인만 다룬다. DSL의 int 도메인
(`runtime.series.arith(..., integer=True)`, 0-방향 절삭 나눗셈)은 배열
전체가 정수인 시리즈에만 의미가 있고 지표·가격 신호는 대부분 float
도메인이라 지금 필요하지 않다 — 필요해지면 새 리프에서 명시적으로 다룬다
(추측으로 지금 만들지 않는다).

순수 모듈 — I/O 없음.
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
    """`BT_VECTOR_SIGNAL` — 배열 길이 불일치·음수 시프트 오프셋 등 fail-closed 거부."""


@dataclass(frozen=True, slots=True)
class BoolSignal:
    """bool 시리즈의 벡터 표현. `Series.of_bools`의 numpy 대응. `na=True`인
    자리의 `values`는 정의되지 않은 자리값(`False`)이며 읽지 않아야 한다."""

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


# ---- DSL Series <-> 벡터 표현 왕복(테스트·향후 BT-15b 브리지용) ----


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


# ---- 산술·비교·교차 ----


def arith(op: ArithOp, left: FloatArray, right: FloatArray) -> FloatArray:
    """`runtime.series.arith(op, left, right, integer=False)`의 원소 단위
    동등물. `NaN`은 IEEE754 전파 규칙으로 이미 na 전파와 같은 결과를 내고,
    0 나눗셈·비유한 결과는 `np.isfinite`로 걸러 `NaN`(na)으로 접는다
    (`_float_arith`의 `_finite` 규칙과 동일)."""
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
    """`runtime.series.compare`의 원소 단위 동등물. 피연산자 중 하나라도
    `NaN`(na)이면 결과도 na다(numpy 비교의 `nan < x = False`는 na가 아니라
    "거짓"을 뜻하므로 여기서 명시적으로 구분한다)."""
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
    """`runtime.series.cross`의 원소 단위 동등물. `runtime.series.cross`와
    달리 스칼라 브로드캐스트를 지원하지 않는다 — 이 모듈은 이미 봉 수만큼
    물질화된 배열만 다룬다(모듈 docstring 스코프)."""
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


# ---- 논리(3치 Kleene) ----


def logical(op: LogicalOp, left: BoolSignal, right: BoolSignal) -> BoolSignal:
    """`runtime.series.logical`의 원소 단위 동등물(Kleene 3치): `and`는 한쪽이
    확정 `False`면 na 여부와 무관하게 `False`, 아니면 na가 하나라도 있으면
    na, 둘 다 확정 `True`면 `True`. `or`는 대칭(한쪽이 확정 `True`면 `True`)."""
    _same_length(left, right)
    ln, rn = left.na, right.na
    lv, rv = left.values, right.values
    if op == "and":
        decided = (~ln & ~lv) | (~rn & ~rv)  # 한쪽이 확정 False
        na = ~decided & (ln | rn)
        values = ~decided & ~na
    elif op == "or":
        decided = (~ln & lv) | (~rn & rv)  # 한쪽이 확정 True
        na = ~decided & (ln | rn)
        values = decided
    else:
        raise VectorSignalError(f"알 수 없는 논리 연산자: {op!r}")
    return BoolSignal(values=values, na=na)


def logical_not(value: BoolSignal) -> BoolSignal:
    return BoolSignal(values=np.where(value.na, False, ~value.values), na=value.na.copy())


# ---- 시프트(인덱싱) ----


def shift_numeric(value: FloatArray, offset: int) -> FloatArray:
    """`s[offset]`의 원소 단위 동등물: 봉 t의 값 = `value[t-offset]`, `t<offset`
    이면 na(`NaN`)."""
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
    """수치 시리즈의 na 여부. 결과 자신은 절대 na가 아니다(`runtime.series`
    모듈 docstring 정의)."""
    mask = np.isnan(value)
    return BoolSignal(values=mask, na=np.zeros(len(value), dtype=np.bool_))
