"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-9(a) —
AIOS Script `math.*` builtins (scalar/series promotion, na propagation).

Pure module (no I/O, no recursion). Injects the `MATH_BUILTINS` table as-is into
the interpreter's (DSL-8) `BuiltinRegistry` — the interpreter only looks it up;
the implementation lives here.

Type/shape rules (aligned with the DSL-4 `typing/types.py` lattice):
- The DSL-4 checker types an `ns.ident(...)` call as: "if all arguments are numeric
  and at least one is a series, the result is `series<float>`, otherwise `float`."
  Hence every `math.*` produces only float-domain values (int is promoted to float,
  no bool return). A function that returns bool, like `is_na`, is not registered
  until the checker can model a bool return (an unverified DSL-4 item — this module
  does not invent a type on its own).
- Promotion: if any argument is a `Series`, scalar arguments are broadcast to
  `bar_count` (from the call site's `CallSite.bar_count`) and computed elementwise,
  so the result is also a series. If all arguments are scalar, the result is scalar
  too. A series whose length differs from the bar count is an error (same as DSL-8
  `broadcast`).
- na propagation: same rule as DSL-8 `series.py` — if any operand is na (None),
  the result is na. Division by zero, out-of-domain inputs (log(0), sqrt(-1)), and
  overflow (exp(1000)) yield na instead of raising. The only exception is `nz`
  (the function whose job is to fill na).
- Domain: bool and non-numeric elements are rejected (`BuiltinCallError`,
  fail-closed). Python `bool` is a subtype of `int`, so it is filtered out
  explicitly.

Function table (`math.<ident>`): abs, sign, floor, ceil, round, sqrt, log, exp
(1 argument), pow (2 arguments), max, min (2+ arguments), nz (1-2 arguments,
default fill 0).
`round` is fixed to half-away-from-zero (0.5→1, -0.5→-1) — Python's built-in
`round` uses banker's rounding, which differs from what script authors expect
(Pine's `math.round`), and since either convention preserves determinism as long
as one rule is picked, the documented one was chosen.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Final

from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value, broadcast

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter_types import Builtin, CallSite

__all__ = ["MATH_BUILTINS", "BuiltinCallError", "apply_elementwise"]

_Kernel = Callable[[Sequence[float]], float | None]


class BuiltinCallError(ScriptRuntimeError):
    """Builtin call rejected. `reason` is the error code the API layer maps (reuses
    the registry's codes).

    Codes: `SCRIPT_BUILTIN_ARITY` (argument count), `SCRIPT_BUILTIN_ARG`
    (domain/shape), and the ones `builtins_ta.py` carries over verbatim from the
    indicator registry/engine: `STRATEGY_INDICATOR_UNKNOWN`,
    `STRATEGY_PARAM_OUT_OF_RANGE`, `INDICATOR_INPUT_INVALID`,
    `INDICATOR_LOOKBACK_INSUFFICIENT`, `INDICATOR_REGISTRY_MISMATCH`.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


def _numeric(v: Scalar, where: str) -> float | None:
    """Validate the numeric domain (None is na). Rejects bool and non-numeric values."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where}: 수치가 아닙니다: {v!r}")
    return float(v)


def apply_elementwise(
    ident: str, args: tuple[Value, ...], bar_count: int, kernel: _Kernel
) -> Value:
    """Broadcast a mix of scalar/series arguments to an elementwise kernel.

    If any element is na, the kernel is not called and na is produced (na
    propagation). The kernel takes a list of floats and returns a float or
    None (na); out-of-domain (`ValueError`), overflow, and non-finite results
    are normalized to na (same rule as DSL-8 `_finite`).
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


# ---- kernels ----


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
    """`nz(x)` / `nz(x, fill)`: na → fill (default 0). If fill itself is na, the
    result is na too.

    Since this function consumes na, it broadcasts directly instead of going
    through `apply_elementwise` (which propagates na).
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
"""`(ns, ident)` → builtin. The interpreter's `default_builtins()` registers this table as-is."""
