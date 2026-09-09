"""DSL-14 — Pine Script v5 partial-grammar AST.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
DSL-14. Only fixes the shape of what `parser.py` produces — grammar rules
and allow/reject decisions are `parser.py`'s job (split out to satisfy the
architecture guard P6.line_cap, following the precedent of AIOS Script's
`grammar/ast.py`+`grammar/parser.py` split). Nodes form a shallow
`dataclass(frozen=True, slots=True)` tree that uses the type itself as the
discriminant instead of a discriminated union — a JSON round trip is not a
requirement for this leaf (transpile is DSL-15's job).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumberLiteral:
    value: int | float


@dataclass(frozen=True, slots=True)
class StringLiteral:
    value: str


@dataclass(frozen=True, slots=True)
class BoolLiteral:
    value: bool


@dataclass(frozen=True, slots=True)
class Identifier:
    name: str


@dataclass(frozen=True, slots=True)
class CallArg:
    name: str | None
    value: Expr


@dataclass(frozen=True, slots=True)
class CallExpr:
    """If `ns is None` this is a bare function call (`plot(...)`); otherwise `ns.ident(...)`."""

    ns: str | None
    ident: str
    args: tuple[CallArg, ...]


@dataclass(frozen=True, slots=True)
class UnaryExpr:
    op: str  # "-"
    operand: Expr


@dataclass(frozen=True, slots=True)
class PostfixExpr:
    """`base[index]` — historical reference only (index is a constant integer >= 0)."""

    base: Expr
    index: int


@dataclass(frozen=True, slots=True)
class NotExpr:
    operand: Expr


@dataclass(frozen=True, slots=True)
class BinaryExpr:
    op: str
    left: Expr
    right: Expr


Expr = (
    NumberLiteral
    | StringLiteral
    | BoolLiteral
    | Identifier
    | CallExpr
    | UnaryExpr
    | PostfixExpr
    | NotExpr
    | BinaryExpr
)


@dataclass(frozen=True, slots=True)
class AssignStmt:
    """`ident = expr` — a single assignment (`parser.py` separately rejects reassignment `:=`)."""

    name: str
    expr: Expr


@dataclass(frozen=True, slots=True)
class ExprStmt:
    expr: Expr


Statement = AssignStmt | ExprStmt


@dataclass(frozen=True, slots=True)
class PineProgram:
    statements: tuple[Statement, ...]
