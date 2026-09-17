"""UX-6 — compiled filter evaluation against a single instrument's field values (pure, no I/O).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/run_screen.py`("execution (cursor pagination/cap), result cache").
`domain/query_plan.py`(UX-5) already proved a `ScreenDefinition` compiles and
contains no future references; this module is the "follow-up leaf" its
docstring points at — it gives the compiled `Expr` tree an actual numeric
meaning against one row of point-in-time field values.

Field registry gap (documented honestly, same posture as query_plan.py):
IND-12(indicator)/fundamental/research field registries do not exist yet, so
this leaf only wires up the OHLCV fields the DC-13 hot storage already has
(`open|high|low|close|volume`, see `RECOGNIZED_FIELD_NAMES`). A condition
referencing any other identifier fails closed at `validate_plan_supported`
(400, not a silent always-empty result) instead of guessing a field mapping.

Multiple `ScreenDefinition.filters` combine with AND (each filter narrows the
result set) — the spec text does not pin this down explicitly, but it is the
conventional screener semantics and is documented here as the leaf's
decision.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from src.core.script.grammar.ast import (
    BinaryExpr,
    CallExpr,
    Expr,
    Identifier,
    NotExpr,
    NumberLiteral,
    PostfixExpr,
    UnaryExpr,
)
from src.foundation.screener.domain.query_plan import CompiledFilter, QueryPlan

__all__ = [
    "RECOGNIZED_FIELD_NAMES",
    "RowFieldMissingError",
    "ScreenerEvaluationError",
    "identifiers_in",
    "matches_filters",
    "required_field_names",
    "sort_key",
    "validate_plan_supported",
]

RECOGNIZED_FIELD_NAMES: frozenset[str] = frozenset({"open", "high", "low", "close", "volume"})

# math.* is the only namespace evaluated directly (no series/history state needed
# for a point-in-time screener row) — ta.*/other namespaces stay unsupported until
# IND-12 exists.
_SUPPORTED_CALLS: Mapping[tuple[str, str], Callable[..., Decimal]] = {
    ("math", "abs"): abs,
    ("math", "min"): min,
    ("math", "max"): max,
}

_UNSUPPORTED_BINARY_OPS = frozenset({"crosses_above", "crosses_below"})

_EVAL_ERROR_CODES = frozenset(
    {
        "SCREENER_EVAL_UNKNOWN_FIELD",
        "SCREENER_EVAL_UNSUPPORTED_CALL",
        "SCREENER_EVAL_UNSUPPORTED_POSTFIX",
        "SCREENER_EVAL_UNSUPPORTED_OP",
    }
)


class ScreenerEvaluationError(Exception):
    """A `QueryPlan` cannot be evaluated by this engine (400) — raised eagerly by
    `validate_plan_supported` before any row is scanned, never mid-scan."""

    def __init__(self, code: str, message: str) -> None:
        if code not in _EVAL_ERROR_CODES:
            raise ValueError(f"code outside the screener evaluation taxonomy: {code!r}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class RowFieldMissingError(Exception):
    """One instrument's row is missing a required field (e.g. no candle yet).

    Fail-closed per-row, not per-plan: the caller (`application/run_screen.py`)
    excludes only this row instead of failing the whole scan or fabricating a
    0/NaN value (same invariant `HotPostgresStorage` documents)."""

    def __init__(self, field_name: str) -> None:
        super().__init__(field_name)
        self.field_name = field_name


def identifiers_in(expr: Expr) -> frozenset[str]:
    """Every field-name identifier a condition expression references."""
    if isinstance(expr, Identifier):
        return frozenset({expr.name})
    if isinstance(expr, NumberLiteral):
        return frozenset()
    if isinstance(expr, CallExpr):
        names: set[str] = set()
        for arg in expr.args:
            names |= identifiers_in(arg)
        return frozenset(names)
    if isinstance(expr, UnaryExpr):
        return identifiers_in(expr.operand)
    if isinstance(expr, NotExpr):
        return identifiers_in(expr.operand)
    if isinstance(expr, PostfixExpr):
        return identifiers_in(expr.base)
    if isinstance(expr, BinaryExpr):
        return identifiers_in(expr.left) | identifiers_in(expr.right)
    raise AssertionError(f"unknown Expr kind: {expr!r}")  # pragma: no cover


def required_field_names(plan: QueryPlan) -> frozenset[str]:
    names: set[str] = set()
    for f in plan.filters:
        names |= identifiers_in(f.condition)
    if plan.sort_field is not None:
        names.add(plan.sort_field)
    names |= set(plan.columns)
    return frozenset(names)


def _validate_expr_supported(expr: Expr) -> None:
    if isinstance(expr, Identifier | NumberLiteral):
        return
    if isinstance(expr, CallExpr):
        if (expr.ns, expr.ident) not in _SUPPORTED_CALLS:
            raise ScreenerEvaluationError(
                "SCREENER_EVAL_UNSUPPORTED_CALL",
                f"{expr.ns}.{expr.ident}() is not supported by the screener engine yet",
            )
        for arg in expr.args:
            _validate_expr_supported(arg)
        return
    if isinstance(expr, UnaryExpr):
        _validate_expr_supported(expr.operand)
        return
    if isinstance(expr, NotExpr):
        _validate_expr_supported(expr.operand)
        return
    if isinstance(expr, PostfixExpr):
        raise ScreenerEvaluationError(
            "SCREENER_EVAL_UNSUPPORTED_POSTFIX",
            "series history indexing (`field[n]`) is not supported — the screener "
            "evaluates a single point-in-time row",
        )
    if isinstance(expr, BinaryExpr):
        if expr.op in _UNSUPPORTED_BINARY_OPS:
            raise ScreenerEvaluationError(
                "SCREENER_EVAL_UNSUPPORTED_OP",
                f"'{expr.op}' needs series history, not supported for a screener row",
            )
        _validate_expr_supported(expr.left)
        _validate_expr_supported(expr.right)
        return
    raise AssertionError(f"unknown Expr kind: {expr!r}")  # pragma: no cover


def validate_plan_supported(plan: QueryPlan) -> None:
    """Raise eagerly (before any DB round trip) if the plan uses a syntax shape or
    field name this execution engine cannot evaluate — see the module docstring's
    field-registry-gap note for why the field check is fail-closed."""
    for f in plan.filters:
        _validate_expr_supported(f.condition)
    unknown = required_field_names(plan) - RECOGNIZED_FIELD_NAMES
    if unknown:
        raise ScreenerEvaluationError(
            "SCREENER_EVAL_UNKNOWN_FIELD",
            f"unrecognized field name(s): {sorted(unknown)}",
        )


def _as_decimal(value: Decimal | bool) -> Decimal:
    if isinstance(value, bool):
        raise AssertionError("bool leaked into arithmetic context")  # pragma: no cover
    return value


def _as_bool(value: Decimal | bool) -> bool:
    if isinstance(value, bool):
        return value
    raise AssertionError("non-bool leaked into a boolean context")  # pragma: no cover


def _eval(expr: Expr, row: Mapping[str, Decimal]) -> Decimal | bool:
    if isinstance(expr, NumberLiteral):
        return Decimal(str(expr.value))
    if isinstance(expr, Identifier):
        try:
            return row[expr.name]
        except KeyError:
            raise RowFieldMissingError(expr.name) from None
    if isinstance(expr, UnaryExpr):
        return -_as_decimal(_eval(expr.operand, row))
    if isinstance(expr, NotExpr):
        return not _as_bool(_eval(expr.operand, row))
    if isinstance(expr, CallExpr):
        fn = _SUPPORTED_CALLS[(expr.ns, expr.ident)]
        args = [_as_decimal(_eval(a, row)) for a in expr.args]
        return fn(*args)
    if isinstance(expr, BinaryExpr):
        if expr.op == "and":
            return _as_bool(_eval(expr.left, row)) and _as_bool(_eval(expr.right, row))
        if expr.op == "or":
            return _as_bool(_eval(expr.left, row)) or _as_bool(_eval(expr.right, row))
        left = _as_decimal(_eval(expr.left, row))
        right = _as_decimal(_eval(expr.right, row))
        if expr.op == "<":
            return left < right
        if expr.op == "<=":
            return left <= right
        if expr.op == "==":
            return left == right
        if expr.op == ">=":
            return left >= right
        if expr.op == ">":
            return left > right
        if expr.op == "+":
            return left + right
        if expr.op == "-":
            return left - right
        if expr.op == "*":
            return left * right
        if expr.op == "/":
            return left / right
        raise AssertionError(f"unsupported binary op reached evaluator: {expr.op!r}")
    raise AssertionError(f"unsupported Expr kind reached evaluator: {expr!r}")  # pragma: no cover


def matches_filters(plan: QueryPlan, row: Mapping[str, Decimal]) -> bool:
    """AND every filter's condition. Raises `RowFieldMissingError` if `row` lacks a
    field a condition needs (caller excludes that row, see the class docstring)."""
    filters: tuple[CompiledFilter, ...] = plan.filters
    return all(_as_bool(_eval(f.condition, row)) for f in filters)


def sort_key(plan: QueryPlan, row: Mapping[str, Decimal]) -> Decimal | None:
    if plan.sort_field is None:
        return None
    return row.get(plan.sort_field)
