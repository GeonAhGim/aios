"""DSL-14 — Pine Script v5 부분 문법 AST.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
DSL-14. `parser.py`가 만들어 내는 산출물의 형태만 고정한다 — 문법 규칙과
허용/거부 판정은 `parser.py`의 몫이다(architecture 가드 P6.line_cap 준수를
위해 분리, AIOS Script `grammar/ast.py`+`grammar/parser.py` 분리 선례를
따른다). 노드는 discriminated union 대신 타입 자체를 판별자로 쓰는 얕은
`dataclass(frozen=True, slots=True)` 트리다 — JSON 왕복은 이 리프의
요구사항이 아니다(transpile은 DSL-15의 몫).
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
    """`ns is None`이면 바깥 함수 호출(`plot(...)`), 아니면 `ns.ident(...)`."""

    ns: str | None
    ident: str
    args: tuple[CallArg, ...]


@dataclass(frozen=True, slots=True)
class UnaryExpr:
    op: str  # "-"
    operand: Expr


@dataclass(frozen=True, slots=True)
class PostfixExpr:
    """`base[index]` — 과거참조만(index는 0 이상 정수 상수)."""

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
    """`ident = expr` — 단일 대입(재대입 `:=`은 `parser.py`가 별도 거부)."""

    name: str
    expr: Expr


@dataclass(frozen=True, slots=True)
class ExprStmt:
    expr: Expr


Statement = AssignStmt | ExprStmt


@dataclass(frozen=True, slots=True)
class PineProgram:
    statements: tuple[Statement, ...]
