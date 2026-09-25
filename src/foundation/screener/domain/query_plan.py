"""Screener query plan — `ScreenDefinition` -> `QueryPlan` (pure, no I/O).

Spec: L4_product_experience_and_discovery_v1.0.md §2.2 `domain/query_plan.py`
("filters -> execution plan (pure). Indicators map to column paths, research
filters require `as_of`"), task-2628(U-1a) decision ("the condition DSL
reuses the AIOS Script boolean-expression subset instead of inventing a new
language").

Reuse strategy: parse each filter's `condition` source to collect the
identifiers (field names) it references, implicitly declare those as
`input <name> : series<float> = 0`, append
`signal __screener_filter = <condition>`, and call only the existing public
DSL-3 (parser) / DSL-4 (type checker) / DSL-5 (lookahead) functions as-is —
this cycle does not extend the `src/core/script/grammar` AST/parser owned by
another worker (same decision as `artifact/compile.py`). The `signal` decl
already enforces "the expression must be bool-typed", so the requirement
that a screener condition is boolean falls out for free.

Default field type: every field identifier is implicitly declared as
`series<float>` — there is no IND-12 indicator/fundamental/research field
registry yet (follow-up leaf), so the real type is unknown. `series<float>`
is the widest numeric type in the promotion lattice, so most
comparison/arithmetic conditions pass, but boolean fields (e.g. `is_active`)
or string/categorical comparisons (the AIOS Script grammar has no string
literal production at all) are not supported at this stage — the execution
engine that wires up the field registry (UX-6, U-1b) replaces this default
with the real type.

IR lowering (DSL-7) and resource limits (DSL-6) are not run here — that is
the job of the engine that actually executes against symbol data (UX-6,
follow-up leaf U-1b). This module only proves that a `ScreenDefinition`
compiles and contains no future references.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_lookahead
from src.core.script.grammar.ast import (
    BinaryExpr,
    CallExpr,
    Expr,
    Identifier,
    NotExpr,
    NumberLiteral,
    PostfixExpr,
    SignalDecl,
    UnaryExpr,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, tokenize
from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import ScriptTypeError, check_program
from src.foundation.screener.contracts.v1 import Filter, ScreenDefinition

_PROBE_DECL_NAME = "__screener_probe"
_FILTER_DECL_NAME = "__screener_filter"
_SYNTHESIZED_FIELD_TYPE = "series<float>"

SCREENER_CONDITION_ERROR_CODES = frozenset(
    {
        "SCREENER_CONDITION_SYNTAX",
        "SCREENER_CONDITION_TYPE",
        "SCREENER_CONDITION_LOOKAHEAD",
    }
)


class ScreenerConditionError(Exception):
    """`Filter.condition` cannot be compiled as an AIOS Script boolean-expression subset.

    Mirrors `src.core.script.artifact.compile.ScriptCompileError`, but the
    screener skips IR lowering and resource-limit checks, so the taxonomy has
    only 3 codes instead of 4.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in SCREENER_CONDITION_ERROR_CODES:
            raise ValueError(f"code outside the screener condition taxonomy: {code!r}")
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CompiledFilter:
    kind: str
    condition_source: str
    condition: Expr


@dataclass(frozen=True)
class QueryPlan:
    universe: str
    filters: tuple[CompiledFilter, ...]
    sort_field: str | None
    sort_direction: str | None
    columns: tuple[str, ...]


def _identifiers_in(expr: Expr) -> frozenset[str]:
    """Collect every identifier (field name) referenced by a condition expression.

    `CallExpr.ns`/`ident` are namespace/function-name strings, not variable
    references, so they are excluded.
    """
    if isinstance(expr, Identifier):
        return frozenset({expr.name})
    if isinstance(expr, NumberLiteral):
        return frozenset()
    if isinstance(expr, CallExpr):
        names: set[str] = set()
        for arg in expr.args:
            names |= _identifiers_in(arg)
        return frozenset(names)
    if isinstance(expr, UnaryExpr):
        return _identifiers_in(expr.operand)
    if isinstance(expr, NotExpr):
        return _identifiers_in(expr.operand)
    if isinstance(expr, PostfixExpr):
        return _identifiers_in(expr.base)
    if isinstance(expr, BinaryExpr):
        return _identifiers_in(expr.left) | _identifiers_in(expr.right)
    raise AssertionError(f"unknown Expr kind: {expr!r}")  # pragma: no cover


def _parse_condition_expr(source: str) -> Expr:
    probe_source = f"signal {_PROBE_DECL_NAME} = {source}"
    try:
        program = parse(probe_source)
    except ScriptSyntaxError as exc:
        raise ScreenerConditionError("SCREENER_CONDITION_SYNTAX", exc.message) from exc
    decl = program.decls[0]
    if not isinstance(decl, SignalDecl):
        raise AssertionError(f"unknown decl kind: {decl!r}")  # pragma: no cover
    return decl.expr


def _compile_condition(source: str) -> Expr:
    raw_expr = _parse_condition_expr(source)
    field_names = sorted(_identifiers_in(raw_expr))
    preamble = "".join(f"input {name} : {_SYNTHESIZED_FIELD_TYPE} = 0\n" for name in field_names)
    full_source = f"{preamble}signal {_FILTER_DECL_NAME} = {source}"
    try:
        tokens = tokenize(full_source)
        program = parse(full_source)
    except ScriptSyntaxError as exc:
        raise ScreenerConditionError("SCREENER_CONDITION_SYNTAX", exc.message) from exc
    try:
        check_program(program)
    except ScriptTypeError as exc:
        raise ScreenerConditionError("SCREENER_CONDITION_TYPE", exc.message) from exc
    try:
        check_lookahead(tokens)
    except ScriptLookaheadError as exc:
        raise ScreenerConditionError("SCREENER_CONDITION_LOOKAHEAD", exc.message) from exc
    final_decl = program.decls[-1]
    if not isinstance(final_decl, SignalDecl):
        raise AssertionError(f"unknown decl kind: {final_decl!r}")  # pragma: no cover
    return final_decl.expr


def _compile_filter(filt: Filter) -> CompiledFilter:
    expr = _compile_condition(filt.condition)
    return CompiledFilter(kind=filt.kind, condition_source=filt.condition, condition=expr)


def build_query_plan(definition: ScreenDefinition) -> QueryPlan:
    """Compile all filters of a `ScreenDefinition` into a plan.

    `ResearchFilter`'s `as_of` requirement is already enforced at the
    contract level (contracts/v1.py) — the field is mandatory, so an
    instance cannot be constructed without it. This function only handles
    condition-source compilation and future-reference rejection
    (`security()`-style calls, negative/variable postfix indices).
    """
    filters = tuple(_compile_filter(f) for f in definition.filters)
    sort_field = definition.sort.field if definition.sort is not None else None
    sort_direction = definition.sort.direction if definition.sort is not None else None
    return QueryPlan(
        universe=definition.universe,
        filters=filters,
        sort_field=sort_field,
        sort_direction=sort_direction,
        columns=definition.columns,
    )
