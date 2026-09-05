"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 표 88행/§9.4 DSL-8 —
AIOS Script 런타임 값 모델: 시리즈(`Series`)와 원소 단위 연산(브로드캐스트).

값은 두 모양이다. `Scalar`(int/float/bool, 봉과 무관한 값)와 `Series`(봉마다
하나씩, 길이 = 봉 수). `None`은 na(결측)다 — Pine의 `na`처럼 "값이 아직
없다"를 뜻하며 별도 센티널 대신 `None` 하나로만 표현한다. 시리즈 원소도
`None`일 수 있다.

§3.3 의미론(이 리프가 확정하는 v1 규칙 — 스펙 문법표에 없는 항목은 여기서
고정하고, 참조 구현 property 테스트가 같은 정의를 독립 구현해 대조한다):
- 인덱싱 `s[n]`(`shift`): 봉 t의 값 = s의 봉 t-n 값, t<n이면 na. 과거만
  본다(음수 n은 문법·DSL-5가 이미 거부, 여기서도 0 미만이면 오류).
- na 전파: 산술·비교·부호 반전·교차는 피연산자 하나라도 na면 na.
- 논리(and/or/not)는 3치(Kleene): `False and na = False`, `True or na = True`,
  그 외 na가 섞이면 na. 신호 소비자는 `True`만 발화로 취급해야 한다(fail-closed —
  na는 "모름"이지 "아니오"가 아니다).
- 0 나눗셈·비유한 결과(inf/nan)는 예외 대신 na. 정수 `/`는 0 방향 절삭(int 정적
  타입 유지 — DSL-4 `promote_numeric`이 int/int→int로 정한 것과 정합).
- `crosses_above(a, b)`: 봉 t에서 `a[t] > b[t] and a[t-1] <= b[t-1]`, t=0 또는
  넷 중 하나라도 na면 na. `crosses_below`는 부등호 반대. 교차는 본질적으로 봉
  차원을 가지므로 피연산자가 둘 다 스칼라여도 결과는 길이 `bar_count` 시리즈다.
- nz/na: `nz(x, fill=0)`은 na를 `fill`로 바꾸고 `is_na(x)`는 na 여부 bool
  시리즈다. 둘 다 시리즈 연산으로만 두고, 스크립트에서 어떤 이름(`math.nz`
  등)으로 노출할지는 DSL-9 빌트인 레지스트리의 몫이다.
- 정수 도메인은 bool을 거부한다(Python `bool`이 `int`의 하위형이라 명시 검사).

순수·I/O 없음·재귀 없음. 길이가 다른 시리즈끼리의 연산은 `ScriptRuntimeError`
(fail-closed — 조용히 자르거나 채우지 않는다).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Literal

Scalar = int | float | bool | None
"""봉과 무관한 값. `None` = na."""

ArithOp = Literal["+", "-", "*", "/"]
CompareOp = Literal["<", "<=", "==", ">=", ">"]
LogicalOp = Literal["and", "or"]
CrossOp = Literal["crosses_above", "crosses_below"]


class ScriptRuntimeError(Exception):
    """IR 실행 실패(모양·도메인 불일치, 길이 불일치, 미등록 빌트인, 미바인딩 이름).

    §3.3 taxonomy는 컴파일 오류 4종만 정의한다. 실행 오류는 "검사를 통과한 IR과
    호스트가 준 입력·레지스트리가 맞지 않음"이므로 별도 코드로 두고 항상 예외로
    낸다(조용한 기본값 없음).
    """

    code: Final = "SCRIPT_RUNTIME"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Series:
    """봉 정렬 시리즈. 원소는 float/bool/None(na). 불변."""

    values: tuple[Scalar, ...]

    @classmethod
    def of_floats(cls, values: Iterable[float | int | None]) -> Series:
        """수치 원소 시리즈. int는 float로 올리고 bool·비유한수는 거부한다."""
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
        """봉 `bar`(0 이상)의 값. 범위 밖이면 오류(조용히 na로 만들지 않는다)."""
        if not 0 <= bar < len(self.values):
            raise ScriptRuntimeError(f"봉 인덱스 범위 밖: {bar} (길이 {len(self.values)})")
        return self.values[bar]

    def shift(self, offset: int) -> Series:
        """`s[offset]` — 봉 t의 값을 s[t-offset]로, 앞 `offset`개 봉은 na."""
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
"""인터프리터 스택 값: 스칼라 또는 시리즈."""


def broadcast(value: Value, bar_count: int) -> Series:
    """스칼라를 길이 `bar_count` 시리즈로 편다. 시리즈면 길이만 확인한다."""
    if isinstance(value, Series):
        if len(value) != bar_count:
            raise ScriptRuntimeError(f"시리즈 길이 불일치: {len(value)} != 봉 수 {bar_count}")
        return value
    return Series((value,) * bar_count)


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


def _int_div(a: int, b: int) -> int:
    """0 방향 절삭 정수 나눗셈(정확, 부동소수 경유 없음). b != 0 전제."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _number(v: Scalar, integer: bool) -> int | float | None:
    """수치 도메인 검증 후 그대로 반환(None은 na). bool·비수치는 거부."""
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
    """원소 단위 이항 커널을 스칼라/시리즈 조합에 브로드캐스트한다."""
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


# ---- 산술 ----


def arith(op: ArithOp, left: Value, right: Value, *, integer: bool) -> Value:
    """+ - * /. `integer=True`면 int 도메인(DSL-4 결과 타입 int), 아니면 float."""

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


# ---- 비교·교차 ----


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
    """교차. 모듈 docstring 정의. 결과는 항상 시리즈(봉 차원 도입)."""
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


# ---- 논리(3치) ----


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


# ---- 인덱싱 ----


def index(value: Value, offset: int) -> Series:
    """`[offset]`. 정적 타입이 시리즈를 보장하므로 스칼라가 오면 IR/입력 불일치."""
    if not isinstance(value, Series):
        raise ScriptRuntimeError(f"'[n]' 인덱싱 대상이 시리즈가 아닙니다: {value!r}")
    return value.shift(offset)
