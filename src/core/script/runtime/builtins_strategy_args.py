"""Argument resolvers for `builtins_strategy.py` (DSL-9b) -- split out at
task-2623 to keep `builtins_strategy.py` under the 300-line architecture cap
for this zone (same reason BT-10's `quick_backtest_fill.py` was split from
`quick_backtest.py`). Each function here validates one `strategy.*` call
argument in isolation: reject a `Series` (must be a script-level constant,
DSL-9b's non-negotiable rule), reject non-numeric/non-finite values, then
apply the field's own range check. No state, no `StrategyBuiltins` instance
access -- callers pass in `where`/`call_index` purely for error messages.

Pure module: no I/O, no clock, no recursion (DoD (e), same as the caller).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Literal

from src.core.script.runtime.builtins_math import BuiltinCallError
from src.core.script.runtime.series import Series, Value

Side = Literal["long", "short"]
OrderTypeName = Literal["market", "limit", "stop"]

__all__ = [
    "resolve_side",
    "resolve_qty",
    "resolve_order_type",
    "resolve_optional_price",
    "resolve_price",
    "resolve_trail_pct",
]


def resolve_side(where: str, value: Value, call_index: int) -> Side:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: side must be a script-level constant, "
            "not a value that varies per bar",
        )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: side is not numeric: {value!r}"
        )
    if value == 1:
        return "long"
    if value == -1:
        return "short"
    raise BuiltinCallError(
        "SCRIPT_BUILTIN_ARG",
        f"{where} call #{call_index}: side must be 1 (long) or -1 (short), got {value!r}",
    )


def resolve_qty(where: str, value: Value, call_index: int) -> float:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: qty must be a script-level constant, "
            "not a value that varies per bar",
        )
    if value is None:
        raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty is na")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty is not numeric: {value!r}"
        )
    qty = float(value)
    if not math.isfinite(qty) or qty <= 0:
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty must be positive, got {qty!r}"
        )
    return qty


def resolve_order_type(where: str, value: Value, call_index: int) -> OrderTypeName:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: order type must be a script-level constant, "
            "not a value that varies per bar",
        )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG",
            f"{where} call #{call_index}: order type is not numeric: {value!r}",
        )
    if value == 1:
        return "limit"
    if value == -1:
        return "stop"
    raise BuiltinCallError(
        "SCRIPT_BUILTIN_ARG",
        f"{where} call #{call_index}: order type must be 1 (limit) or -1 (stop), got {value!r}",
    )


def resolve_optional_price(where: str, value: Value, call_index: int, field: str) -> Decimal | None:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: {field} must be a script-level constant, "
            "not a value that varies per bar",
        )
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: {field} is not numeric: {value!r}"
        )
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG",
            f"{where} call #{call_index}: {field} must be positive, got {price!r}",
        )
    return Decimal(str(price))


def resolve_price(where: str, value: Value, call_index: int, field: str) -> Decimal:
    price = resolve_optional_price(where, value, call_index, field)
    if price is None:
        raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: {field} is na")
    return price


def resolve_trail_pct(where: str, value: Value, call_index: int) -> Decimal | None:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: trail_pct must be a script-level constant, "
            "not a value that varies per bar",
        )
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG",
            f"{where} call #{call_index}: trail_pct is not numeric: {value!r}",
        )
    pct = float(value)
    if not math.isfinite(pct) or not 0 < pct < 1:
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG",
            f"{where} call #{call_index}: trail_pct must be in (0, 1), got {pct!r}",
        )
    return Decimal(str(pct))
