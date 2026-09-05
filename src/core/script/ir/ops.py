"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 표 87행/§9.4 DSL-7 —
AIOS Script IR(`IR_VERSION`) 명령 집합과 결정론 직렬화.

스택 기반 평탄 명령열이다. 각 decl은 명령 몇 개로 내려가고, decl 하나가
끝날 때마다 스택은 비어 있어야 한다(`verify_stack`). 표현식 명령은
post-order(피연산자 먼저, 왼쪽부터)로 놓이므로 인터프리터(DSL-8)는 앞에서
뒤로 한 번만 훑으면 된다 — 재귀·점프·루프 명령은 없다(§3.3 문법에 반복·
재귀가 없으니 IR에도 없다. 결정론은 "표현 불가"로 강제한다, DSL-1 decision).

타입 주석: DSL-4 검사기가 확정한 정적 타입(`Type`, 5종)을 값을 만들어 내는
명령마다 실어 둔다. 인터프리터가 시리즈/스칼라 승격을 다시 추론하지 않고
IR만 보고 결정하게 하기 위함이다(I-05: 백테스트·라이브가 같은 컴파일
산출물을 공유 — 산출물이 자기완결적이어야 한다).

의미 미정의 피연산자(`Order.side/qty_expr/opts`, `Plot.style`)는 §3.3에 별도
프로덕션이 없어 DSL-4가 검사하지 않는다(`typing/checker.py` 모듈 docstring).
IR도 이를 스택 코드로 "해석"하지 않고 DSL-1 AST 노드 그대로 운반한다 —
`buy` 같은 미선언 식별자에 임의 의미를 붙이지 않는다. 의미 확정은 DSL-8/11.

결정론(DoD "같은 AST=같은 IR 바이트"): `to_bytes`는 pydantic JSON 덤프를
`sort_keys=True`·고정 구분자·ASCII로 인코딩한다. 딕셔너리 삽입 순서·해시
시드에 의존하는 경로가 없고, `ConstFloat`는 유한수만 허용해 `NaN`/`Infinity`
같은 비표준 JSON 토큰이 바이트열에 들어가는 일을 막는다(파서는 아주 긴
십진 리터럴에서 `inf`를 만들 수 있다 — 값 수준에서 여기서 거부).
"""
from __future__ import annotations

import json
import math
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.script.grammar.ast import GRAMMAR_VERSION, BinaryOp, Expr
from src.core.script.typing.types import Type

IR_VERSION: Final = "aios-ir-1"


class IRNode(BaseModel):
    """모든 IR 노드의 베이스 — 불변·미지 필드 거부(AST `ScriptNode`와 같은 이유)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- 표현식 명령: 스택에 값을 push/pop ----


class ConstInt(IRNode):
    op: Literal["const_int"] = "const_int"
    value: int


class ConstFloat(IRNode):
    op: Literal["const_float"] = "const_float"
    value: float

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError(f"IR 상수는 유한수여야 합니다(받음: {value!r})")
        return value


class Load(IRNode):
    """선언된 이름(input/let/signal)의 값을 push."""

    op: Literal["load"] = "load"
    name: str
    type: Type


class Neg(IRNode):
    """단항 '-': pop 1, push 1."""

    op: Literal["neg"] = "neg"
    type: Type


class Not(IRNode):
    """'not': pop 1, push 1."""

    op: Literal["not"] = "not"
    type: Type


class Index(IRNode):
    """postfix `[n]`: 시리즈 pop 1, `offset`봉 전 원소 push 1. `type`은 원소 타입."""

    op: Literal["index"] = "index"
    offset: int = Field(ge=0)
    type: Type


class BinOp(IRNode):
    """이항 연산: pop 2(left 아래, right 위), push 1. `type`은 승격 결과."""

    op: Literal["binop"] = "binop"
    operator: BinaryOp
    type: Type


class Call(IRNode):
    """`ns.ident(args)`: pop `argc`(첫 인자가 가장 아래), push 1."""

    op: Literal["call"] = "call"
    ns: str
    ident: str
    argc: int = Field(ge=0)
    type: Type


# ---- decl 명령: 스택을 비운다 ----


class DeclareInput(IRNode):
    op: Literal["declare_input"] = "declare_input"
    name: str
    type: Type
    value: int | float | bool


class Store(IRNode):
    """`let name = expr`: pop 1을 `name`에 바인딩."""

    op: Literal["store"] = "store"
    name: str
    type: Type


class Plot(IRNode):
    """`plot(expr[, style])`: pop 1(expr). `style`은 의미 미정의 → AST 원형 운반."""

    op: Literal["plot"] = "plot"
    type: Type
    style: Expr | None = None


class Signal(IRNode):
    """`signal name = expr`: pop 1을 신호 `name`에 바인딩."""

    op: Literal["signal"] = "signal"
    name: str
    type: Type


class Order(IRNode):
    """`order(side, qty_expr[, opts]) when expr`: pop 1(when). 나머지는 AST 원형 운반."""

    op: Literal["order"] = "order"
    side: Expr
    qty_expr: Expr
    opts: Expr | None = None
    when_type: Type


Instr = Annotated[
    ConstInt
    | ConstFloat
    | Load
    | Neg
    | Not
    | Index
    | BinOp
    | Call
    | DeclareInput
    | Store
    | Plot
    | Signal
    | Order,
    Field(discriminator="op"),
]

_DECL_OPS: Final = frozenset({"declare_input", "store", "plot", "signal", "order"})


class IRProgram(IRNode):
    ir_version: Literal["aios-ir-1"] = IR_VERSION
    grammar_version: Literal["aios-script-1"] = GRAMMAR_VERSION
    instrs: tuple[Instr, ...] = ()


for _cls in (Plot, Order, IRProgram):
    _cls.model_rebuild()


# ---- 직렬화(결정론) ----


def to_bytes(ir: IRProgram) -> bytes:
    """IR → 정규화 JSON 바이트. 같은 IR이면 언제·어디서 호출해도 같은 바이트.

    `sort_keys`로 키 순서를, 고정 구분자로 공백을, `ensure_ascii`로 유니코드
    이스케이프 표기를 고정한다. `allow_nan=False`는 `ConstFloat` 검증의 2차
    방어다(비표준 토큰이 섞이면 예외로 fail-closed).
    """
    return json.dumps(
        ir.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def from_bytes(data: bytes) -> IRProgram:
    """`to_bytes`의 역. 버전 불일치·미지 필드·비유한 상수는 거부."""
    return IRProgram.model_validate(json.loads(data.decode("utf-8")))


# ---- 스택 규율 검증 ----


class IRStackError(Exception):
    """IR 명령열이 스택 규율(언더플로·decl 뒤 잔여값)을 깨뜨림 — 잘못 만들어진 IR."""


def stack_effect(instr: Instr) -> tuple[int, int]:
    """명령의 (pop 수, push 수). 인터프리터·검증기가 공유하는 단일 정의."""
    if isinstance(instr, ConstInt | ConstFloat | Load):
        return (0, 1)
    if isinstance(instr, Neg | Not | Index):
        return (1, 1)
    if isinstance(instr, BinOp):
        return (2, 1)
    if isinstance(instr, Call):
        return (instr.argc, 1)
    if isinstance(instr, DeclareInput):
        return (0, 0)
    if isinstance(instr, Store | Plot | Signal | Order):
        return (1, 0)
    raise IRStackError(f"알 수 없는 IR 명령: {instr!r}")


def verify_stack(ir: IRProgram) -> None:
    """명령열을 한 번 훑어 (1) 언더플로 없음 (2) decl 명령 직후 스택 비어 있음
    (3) 끝에서 스택 비어 있음을 확인한다. 위반은 `IRStackError`.

    `lower_program`이 산출물마다 호출하지만, 바이트에서 복원한 IR(`from_bytes`)을
    실행 전에 다시 검증하는 용도로도 공개한다(I-07: 실패를 실제로 낼 수 있어야 함).
    """
    depth = 0
    for pos, instr in enumerate(ir.instrs):
        pops, pushes = stack_effect(instr)
        if depth < pops:
            raise IRStackError(
                f"#{pos} {instr.op}: 스택 언더플로(필요 {pops}, 현재 {depth})"
            )
        depth = depth - pops + pushes
        if instr.op in _DECL_OPS and depth != 0:
            raise IRStackError(f"#{pos} {instr.op}: decl 뒤 스택 잔여값 {depth}개")
    if depth != 0:
        raise IRStackError(f"명령열 끝 스택 잔여값 {depth}개")
