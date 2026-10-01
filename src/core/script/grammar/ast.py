"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-1 —
the immutable AST for AIOS Script grammar v1 (`GRAMMAR_VERSION`).

This module fixes only the "shape" of what the parser (DSL-3) produces —
actual parsing, static type checking, and look-ahead detection are each
the responsibility of DSL-3/4/5. Nodes are defined with pydantic
`frozen=True` (this codebase's value-object convention, e.g.
`src/core/risk/decision.py`), and are a discriminated union tagged by the
`kind` field, so a `model_dump(mode="json")`/`model_validate` round trip
is an identity — DSL-7 (IR authoring) and DSL-12 (script_hash) depend on
this property.

Productions outside the §3.3 grammar table (loops, recursion,
`security()`-style calls, raw bool literals — tokens not in `primary`)
get no node at all — enforcing determinism and the look-ahead ban via
"cannot be constructed" is a grammar-level invariant that holds even
before the static detector (DSL-5) runs (see decision). Nonterminals
without a dedicated §3.3 production, like `side`/`qty_expr`/`opts`/
`style`, are all taken as plain `Expr`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GRAMMAR_VERSION: Final = "aios-script-1"

_IDENT_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(value: str) -> str:
    if not _IDENT_RE.match(value):
        raise ValueError(f"유효하지 않은 식별자: {value!r}")
    return value


def _reject_bool(value: Any) -> Any:
    """`bool` is a subclass of `int`, so pydantic silently promotes it to
    `int` — to enforce the invariant that §3.3 `primary` has no raw bool
    literal at the value level too, it must be rejected explicitly."""
    if isinstance(value, bool):
        raise ValueError("bool 값은 허용되지 않음")
    return value


class ScriptNode(BaseModel):
    """Common base for all AST nodes — immutable (frozen), rejects unknown
    fields (extra=forbid).

    Without `extra="forbid"`, unknown fields would be silently dropped,
    making the serialization round trip look identity-preserving "by
    accident" while actually hiding information loss.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- type := "int"|"float"|"bool"|"string"|"series<float>"|"series<bool>"|"array<float>" ----
TypeName = Literal[
    "int", "float", "bool", "string", "series<float>", "series<bool>", "array<float>"
]


class TypeNode(ScriptNode):
    kind: Literal["type"] = "type"
    name: TypeName


# ---- primary := NUMBER | ident | call | "(" expr ")" ----
# Parenthesized grouping is only a precedence expression and needs no
# dedicated node.


class NumberLiteral(ScriptNode):
    kind: Literal["number"] = "number"
    value: int | float

    @field_validator("value", mode="before")
    @classmethod
    def _check_value(cls, value: Any) -> Any:
        return _reject_bool(value)


class StringLiteral(ScriptNode):
    kind: Literal["string"] = "string"  # never numeric/bool (M2-3 step 1)
    value: str


# ---- array_literal := "[" (expr ("," expr)*)? "]" — M2-3 step 2 (task-8694) ----
# constant-length vector literal, typed `array<float>`. Elements are checked
# numeric-scalar (not series, not nested array) by DSL-4 (`typing/checker.py`);
# this node only fixes the shape.


class ArrayLiteral(ScriptNode):
    kind: Literal["array"] = "array"
    elements: tuple[Expr, ...] = ()


class Identifier(ScriptNode):
    kind: Literal["ident"] = "ident"
    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_ident(value)


class CallExpr(ScriptNode):
    """call := ns "." ident "(" args ")" — e.g. ta.*, math.*, series.*."""

    kind: Literal["call"] = "call"
    ns: str
    ident: str
    args: tuple[Expr, ...] = ()

    @field_validator("ns", "ident")
    @classmethod
    def _check_names(cls, value: str) -> str:
        return _validate_ident(value)


# ---- unary := "-" unary | postfix ----


class UnaryExpr(ScriptNode):
    kind: Literal["unary"] = "unary"
    op: Literal["-"]
    operand: Expr


# ---- postfix := primary ("[" INT "]")?  — only past references are allowed (constant n>=0). ----
# Fixing the index type itself to `int` makes a "variable index"
# structurally impossible to express, and `ge=0` rejects "negative
# (look-ahead)" at the value level.


class PostfixExpr(ScriptNode):
    kind: Literal["postfix"] = "postfix"
    base: Expr
    index: int | None = Field(default=None, ge=0)

    @field_validator("index", mode="before")
    @classmethod
    def _check_index(cls, value: Any) -> Any:
        return _reject_bool(value)


# ---- not_expr := "not" not_expr | cmp ----


class NotExpr(ScriptNode):
    kind: Literal["not"] = "not"
    operand: Expr


# ---- or/and/cmp/arith/term: all uniformly expressed as left-associative binary operations ----

BinaryOp = Literal[
    "or",
    "and",
    "<",
    "<=",
    "==",
    ">=",
    ">",
    "crosses_above",
    "crosses_below",
    "+",
    "-",
    "*",
    "/",
]


class BinaryExpr(ScriptNode):
    kind: Literal["binary"] = "binary"
    op: BinaryOp
    left: Expr
    right: Expr


# ---- request "(" STRING "," STRING "," expr ")" ----
# An extension beyond the original §3.3 grammar table (M2-2a,
# ADR-2026-09-09-B) — requests a series from another symbol/timeframe.
# The parser (DSL-3) only accepts symbol/timeframe as STRING tokens,
# fixing them as compile-time constants (dynamic symbols are banned):
# declaring this field as `str` makes a "non-literal value" impossible
# to construct even at the AST level. MTF runtime evaluation and
# look-ahead detection belong to M2-2b (a follow-up leaf), so only the
# structure is fixed here.


class RequestExpr(ScriptNode):
    kind: Literal["request"] = "request"
    symbol: str
    timeframe: str
    expr: Expr

    @field_validator("symbol", "timeframe")
    @classmethod
    def _check_nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError("request()의 symbol/timeframe은 빈 문자열일 수 없습니다")
        return value


Expr = Annotated[
    NumberLiteral
    | StringLiteral
    | ArrayLiteral
    | Identifier
    | CallExpr
    | UnaryExpr
    | PostfixExpr
    | NotExpr
    | BinaryExpr
    | RequestExpr,
    Field(discriminator="kind"),
]


# ---- decl := input | let | plot | signal | order ----


class InputDecl(ScriptNode):
    """input ident ":" type "=" literal"""

    kind: Literal["input"] = "input"
    name: str
    type: TypeNode
    value: int | float | bool

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_ident(value)


class LetDecl(ScriptNode):
    kind: Literal["let"] = "let"
    name: str
    expr: Expr

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_ident(value)


class PlotDecl(ScriptNode):
    """plot "(" expr ("," style)? ")" — `style` has no dedicated §3.3
    definition, so it is `Expr`."""

    kind: Literal["plot"] = "plot"
    expr: Expr
    style: Expr | None = None


class SignalDecl(ScriptNode):
    kind: Literal["signal"] = "signal"
    name: str
    expr: Expr

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_ident(value)


class OrderDecl(ScriptNode):
    """order "(" side "," qty_expr ("," opts)? ")" "when" expr

    `side`/`qty_expr`/`opts` have no dedicated §3.3 production, so they
    are all taken as `Expr` only (no extension beyond the grammar table).
    """

    kind: Literal["order"] = "order"
    side: Expr
    qty_expr: Expr
    opts: Expr | None = None
    when: Expr


Decl = Annotated[
    InputDecl | LetDecl | PlotDecl | SignalDecl | OrderDecl,
    Field(discriminator="kind"),
]


class Program(ScriptNode):
    """program := decl*"""

    kind: Literal["program"] = "program"
    grammar_version: Literal["aios-script-1"] = GRAMMAR_VERSION
    decls: tuple[Decl, ...] = ()


for _cls in (
    ArrayLiteral,
    CallExpr,
    UnaryExpr,
    PostfixExpr,
    NotExpr,
    BinaryExpr,
    RequestExpr,
    LetDecl,
    PlotDecl,
    SignalDecl,
    OrderDecl,
    Program,
):
    _cls.model_rebuild()


def to_dict(node: ScriptNode) -> dict[str, Any]:
    """Any AST node → JSON-compatible dict. Half of the serialization round trip (encode)."""
    return node.model_dump(mode="json")


def program_from_dict(data: Mapping[str, Any]) -> Program:
    """dict → `Program`. A `grammar_version` mismatch or unknown field is rejected (fail-closed)."""
    return Program.model_validate(data)
