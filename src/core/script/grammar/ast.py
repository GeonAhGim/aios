"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-1 —
AIOS Script 문법 v1(`GRAMMAR_VERSION`)의 불변 AST.

이 모듈은 파서(DSL-3)가 만들어 내는 산출물의 "형태"만 고정한다 — 실제
파싱·정적 타입 검사·미래참조 검출은 각각 DSL-3/4/5의 책임이다. 노드는
pydantic `frozen=True`(이 코드베이스의 값 객체 관례, 예:
`src/core/risk/decision.py`)로 정의하고, `kind` 판별 필드로 태그된
discriminated union이라 `model_dump(mode="json")`/`model_validate` 왕복이
항등이다 — DSL-7(IR 저작)·DSL-12(script_hash)가 이 성질에 의존한다.

§3.3 문법표 밖의 프로덕션(반복문·재귀·`security()`류, 원시 bool 리터럴 등
`primary`에 없는 토큰)은 노드조차 두지 않는다 — 결정론·미래참조 금지는
"만들 수 없다"로 강제하는 편이 정적 검출기(DSL-5)보다 먼저 성립하는 문법
수준 불변식이다(decision 참조). `side`/`qty_expr`/`opts`/`style`처럼
§3.3에 별도 프로덕션이 없는 논터미널은 전부 일반 `Expr`로만 받는다.
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


class ScriptNode(BaseModel):
    """모든 AST 노드의 공통 베이스 — 불변(frozen)·미지 필드 거부(extra=forbid).

    `extra="forbid"`가 없으면 알 수 없는 필드가 조용히 버려져 직렬화
    왕복이 "우연히" 항등처럼 보일 뿐 실제로는 정보 손실을 감추게 된다.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- type := "int" | "float" | "bool" | "series<float>" | "series<bool>" ----

TypeName = Literal["int", "float", "bool", "series<float>", "series<bool>"]


class TypeNode(ScriptNode):
    kind: Literal["type"] = "type"
    name: TypeName


# ---- primary := NUMBER | ident | call | "(" expr ")" ----
# 괄호 그룹핑은 우선순위 표현일 뿐 별도 노드가 필요 없다.


class NumberLiteral(ScriptNode):
    kind: Literal["number"] = "number"
    value: int | float


class Identifier(ScriptNode):
    kind: Literal["ident"] = "ident"
    name: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_ident(value)


class CallExpr(ScriptNode):
    """call := ns "." ident "(" args ")" — ta.*, math.*, series.* 등."""

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


# ---- postfix := primary ("[" INT "]")?  — 과거참조만 허용(상수 n>=0). ----
# 인덱스 타입 자체를 `int`로 고정해 "변수 인덱스"는 구조적으로 표현 불가능하게
# 하고, `ge=0`로 "음수(미래참조)"를 값 수준에서 거부한다.


class PostfixExpr(ScriptNode):
    kind: Literal["postfix"] = "postfix"
    base: Expr
    index: int | None = Field(default=None, ge=0)


# ---- not_expr := "not" not_expr | cmp ----


class NotExpr(ScriptNode):
    kind: Literal["not"] = "not"
    operand: Expr


# ---- or/and/cmp/arith/term: 전부 좌결합 이항 연산으로 통일 표현 ----

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


Expr = Annotated[
    NumberLiteral | Identifier | CallExpr | UnaryExpr | PostfixExpr | NotExpr | BinaryExpr,
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
    """plot "(" expr ("," style)? ")" — style은 §3.3에 별도 정의 없어 Expr."""

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

    `side`/`qty_expr`/`opts`는 §3.3에 별도 프로덕션이 없어 모두 `Expr`로만
    받는다(문법표 밖 확장 금지).
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
    CallExpr,
    UnaryExpr,
    PostfixExpr,
    NotExpr,
    BinaryExpr,
    LetDecl,
    PlotDecl,
    SignalDecl,
    OrderDecl,
    Program,
):
    _cls.model_rebuild()


def to_dict(node: ScriptNode) -> dict[str, Any]:
    """임의 AST 노드 → JSON 호환 dict. 직렬화 왕복의 절반(encode)."""
    return node.model_dump(mode="json")


def program_from_dict(data: Mapping[str, Any]) -> Program:
    """dict → `Program`. `grammar_version` 불일치·미지 필드는 거부(fail-closed)."""
    return Program.model_validate(data)
