"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-9(a) —
AIOS Script `math.*` 내장함수(스칼라·시리즈 승격, na 전파).

순수 모듈(I/O·재귀 없음). 인터프리터(DSL-8)의 `BuiltinRegistry`에 `MATH_BUILTINS`
표를 그대로 주입한다 — 인터프리터는 조회만 하고 본체는 여기 있다.

타입·모양 규칙(DSL-4 `typing/types.py` 격자와 정합):
- DSL-4 검사기는 `ns.ident(...)` 호출을 "인자가 전부 수치 계열이고, 하나라도
  시리즈면 결과는 `series<float>`, 아니면 `float`"로 타입한다. 따라서 모든 `math.*`는
  float 도메인 값만 낸다(int도 float로 올림, bool 반환 없음). `is_na`처럼 bool을
  내는 함수는 검사기가 bool 반환을 모델링하기 전까지 등록하지 않는다(DSL-4 미검증
  항목 — 여기서 임의로 타입을 만들지 않는다).
- 승격: 인자 중 하나라도 `Series`면 스칼라 인자를 `bar_count`(호출 문맥
  `CallSite.bar_count`)로 펴서 원소 단위로 계산하고 결과도 시리즈다. 전부 스칼라면
  결과도 스칼라다. 시리즈 길이가 봉 수와 다르면 오류(DSL-8 `broadcast`와 동일).
- na 전파: DSL-8 `series.py`와 같은 규칙 — 피연산자 하나라도 na(None)면 na.
  0 나눗셈·정의역 밖(log(0)·sqrt(-1))·오버플로(exp(1000))처럼 비유한 결과는
  예외 대신 na. 유일한 예외는 `nz`(na를 채우는 함수 자체).
- 도메인: bool·비수치 원소는 거부(`BuiltinCallError`, fail-closed). Python `bool`이
  `int`의 하위형이라 명시적으로 걸러낸다.

함수 표(`math.<ident>`): abs·sign·floor·ceil·round·sqrt·log·exp(1인자),
pow(2인자), max·min(2인자 이상), nz(1~2인자, 기본 채움 0).
`round`는 half-away-from-zero(0.5→1, -0.5→-1)로 고정한다 — Python 내장 `round`의
은행가 반올림은 스크립트 작성자 기대(Pine `math.round`)와 다르고, 어느 쪽이든 한
규칙으로 결정론이 유지되면 되므로 문서화한 쪽을 택했다.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Final

from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value, broadcast

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter import Builtin, CallSite

__all__ = ["MATH_BUILTINS", "BuiltinCallError", "apply_elementwise"]

_Kernel = Callable[[Sequence[float]], float | None]


class BuiltinCallError(ScriptRuntimeError):
    """빌트인 호출 거부. `reason`은 API 계층이 매핑할 오류 코드(레지스트리 코드 재사용).

    코드: `SCRIPT_BUILTIN_ARITY`(인자 개수), `SCRIPT_BUILTIN_ARG`(도메인·모양),
    그리고 `builtins_ta.py`가 지표 레지스트리/엔진에서 그대로 옮기는
    `STRATEGY_INDICATOR_UNKNOWN`·`STRATEGY_PARAM_OUT_OF_RANGE`·`INDICATOR_INPUT_INVALID`·
    `INDICATOR_LOOKBACK_INSUFFICIENT`·`INDICATOR_REGISTRY_MISMATCH`.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


def _numeric(v: Scalar, where: str) -> float | None:
    """수치 도메인 검증(None은 na). bool·비수치는 거부."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where}: 수치가 아닙니다: {v!r}")
    return float(v)


def apply_elementwise(
    ident: str, args: tuple[Value, ...], bar_count: int, kernel: _Kernel
) -> Value:
    """스칼라/시리즈 혼합 인자를 원소 단위 커널에 브로드캐스트한다.

    원소 중 하나라도 na면 커널을 부르지 않고 na를 낸다(na 전파). 커널은 float 목록을
    받아 float 또는 None(na)을 돌려주며, 정의역 밖(`ValueError`)·오버플로·비유한
    결과는 na로 정규화한다(DSL-8 `_finite`와 동일 규칙).
    """
    where = f"math.{ident}()"
    if not any(isinstance(a, Series) for a in args):
        return _eval(kernel, [_numeric(a, where) for a in args if not isinstance(a, Series)])
    columns: list[tuple[Scalar, ...]] = []
    for i, a in enumerate(args):
        try:
            columns.append(broadcast(a, bar_count).values)
        except ScriptRuntimeError as exc:
            raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where} 인자 #{i + 1}: {exc}") from exc
    out: list[Scalar] = []
    for t in range(bar_count):
        row = [_numeric(col[t], f"{where} 봉 #{t}") for col in columns]
        out.append(_eval(kernel, row))
    return Series(tuple(out))


def _eval(kernel: _Kernel, row: list[float | None]) -> float | None:
    if any(v is None for v in row):
        return None
    try:
        result = kernel([v for v in row if v is not None])
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return None if result is None else _finite(float(result))


# ---- 커널 ----


def _round_half_away(x: float) -> float:
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def _pow(row: Sequence[float]) -> float | None:
    base, exponent = row
    return math.pow(base, exponent)


_UNARY: Final[dict[str, Callable[[float], float]]] = {
    "abs": abs,
    "sign": _sign,
    "floor": lambda x: float(math.floor(x)),
    "ceil": lambda x: float(math.ceil(x)),
    "round": _round_half_away,
    "sqrt": math.sqrt,
    "log": math.log,
    "exp": math.exp,
}


def _check_arity(ident: str, argc: int, lo: int, hi: int | None) -> None:
    if argc < lo or (hi is not None and argc > hi):
        expected = f"{lo}" if hi == lo else (f"{lo} 이상" if hi is None else f"{lo}~{hi}")
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARITY", f"math.{ident}() 인자 {expected}개 필요(받음 {argc})"
        )


def _make_unary(ident: str, fn: Callable[[float], float]) -> Builtin:
    def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
        _check_arity(ident, len(args), 1, 1)
        return apply_elementwise(ident, args, site.bar_count, lambda row: fn(row[0]))

    return builtin


def _pow_builtin(args: tuple[Value, ...], site: CallSite) -> Value:
    _check_arity("pow", len(args), 2, 2)
    return apply_elementwise("pow", args, site.bar_count, _pow)


def _make_variadic(ident: str, fn: Callable[[Sequence[float]], float]) -> Builtin:
    def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
        _check_arity(ident, len(args), 2, None)
        return apply_elementwise(ident, args, site.bar_count, fn)

    return builtin


def _nz(args: tuple[Value, ...], site: CallSite) -> Value:
    """`nz(x)` / `nz(x, fill)`: na → fill(기본 0). fill 자체가 na면 결과도 na.

    na를 소비하는 함수라 `apply_elementwise`(na 전파)를 쓰지 않고 직접 편다.
    """
    _check_arity("nz", len(args), 1, 2)
    where = "math.nz()"
    fill_value: Value = args[1] if len(args) == 2 else 0.0
    if not isinstance(args[0], Series) and not isinstance(fill_value, Series):
        x, fill = _numeric(args[0], where), _numeric(fill_value, where)
        return fill if x is None else x
    xs = broadcast(args[0], site.bar_count).values
    fills = broadcast(fill_value, site.bar_count).values
    out: list[Scalar] = []
    for t in range(site.bar_count):
        x, fill = _numeric(xs[t], f"{where} 봉 #{t}"), _numeric(fills[t], f"{where} 봉 #{t}")
        out.append(fill if x is None else x)
    return Series(tuple(out))


def _table() -> dict[tuple[str, str], Builtin]:
    table: dict[tuple[str, str], Builtin] = {
        ("math", ident): _make_unary(ident, fn) for ident, fn in _UNARY.items()
    }
    table[("math", "pow")] = _pow_builtin
    table[("math", "max")] = _make_variadic("max", max)
    table[("math", "min")] = _make_variadic("min", min)
    table[("math", "nz")] = _nz
    return table


MATH_BUILTINS: Final[dict[tuple[str, str], Builtin]] = _table()
"""`(ns, ident)` → 빌트인. 인터프리터 `default_builtins()`가 이 표를 그대로 등록한다."""
