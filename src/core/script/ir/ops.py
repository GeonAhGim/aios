"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 table row 87/§9.4 DSL-7 —
AIOS Script IR (`IR_VERSION`) instruction set and deterministic serialization.

A stack-based flat instruction stream. Each decl lowers to a handful of
instructions, and the stack must be empty whenever a decl ends (`verify_stack`).
Expression instructions are laid out in post-order (operands first, left to
right), so the interpreter (DSL-8) only needs a single forward pass — there
are no recursion/jump/loop instructions (the §3.3 grammar has no iteration or
recursion, so neither does the IR. Determinism is enforced by making it
"inexpressible", DSL-1 decision).

Type annotations: the static type (`Type`, 5 kinds) that the DSL-4 checker
resolved is carried on every instruction that produces a value. This lets the
interpreter decide series/scalar promotion from the IR alone instead of
re-inferring it (I-05: backtest and live share the same compiled artifact —
the artifact must be self-contained).

Operands with undefined semantics (`Order.side/qty_expr/opts`, `Plot.style`)
have no separate production in §3.3, so DSL-4 does not check them
(see the `typing/checker.py` module docstring). The IR likewise does not
"interpret" them as stack code — it carries the DSL-1 AST node as-is, so an
undeclared identifier like `buy` is never assigned an arbitrary meaning.
Semantics are resolved later, in DSL-8/11.

Determinism (DoD "same AST = same IR bytes"): `to_bytes` encodes the pydantic
JSON dump with `sort_keys=True`, fixed separators, and ASCII. No path depends
on dict insertion order or hash seed, and `ConstFloat` allows only finite
numbers so that non-standard JSON tokens like `NaN`/`Infinity` never make it
into the byte stream (the parser can produce `inf` from a very long decimal
literal — rejected here at the value level).
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
    """Base for all IR nodes — immutable, rejects unknown fields (same reason as
    AST `ScriptNode`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# ---- Expression instructions: push/pop values on the stack ----


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
    """Push the value of a declared name (input/let/signal)."""

    op: Literal["load"] = "load"
    name: str
    type: Type


class Neg(IRNode):
    """Unary '-': pop 1, push 1."""

    op: Literal["neg"] = "neg"
    type: Type


class Not(IRNode):
    """'not': pop 1, push 1."""

    op: Literal["not"] = "not"
    type: Type


class Index(IRNode):
    """postfix `[n]`: pop 1 series, push 1 element from `offset` bars back.
    `type` is the element type."""

    op: Literal["index"] = "index"
    offset: int = Field(ge=0)
    type: Type


class BinOp(IRNode):
    """Binary op: pop 2 (left below, right above), push 1. `type` is the promotion result."""

    op: Literal["binop"] = "binop"
    operator: BinaryOp
    type: Type


class Call(IRNode):
    """`ns.ident(args)`: pop `argc` (first arg at the bottom), push 1."""

    op: Literal["call"] = "call"
    ns: str
    ident: str
    argc: int = Field(ge=0)
    type: Type


# ---- decl instructions: these empty the stack ----


class DeclareInput(IRNode):
    op: Literal["declare_input"] = "declare_input"
    name: str
    type: Type
    value: int | float | bool


class Store(IRNode):
    """`let name = expr`: pop 1 and bind it to `name`."""

    op: Literal["store"] = "store"
    name: str
    type: Type


class Plot(IRNode):
    """`plot(expr[, style])`: pop 1 (expr). `style` has undefined semantics →
    carried as the raw AST."""

    op: Literal["plot"] = "plot"
    type: Type
    style: Expr | None = None


class Signal(IRNode):
    """`signal name = expr`: pop 1 and bind it to signal `name`."""

    op: Literal["signal"] = "signal"
    name: str
    type: Type


class Order(IRNode):
    """`order(side, qty_expr[, opts]) when expr`: pop 1 (when). The rest is
    carried as the raw AST."""

    op: Literal["order"] = "order"
    side: Expr
    qty_expr: Expr
    opts: Expr | None = None
    when_type: Type


class Request(IRNode):
    """`request(symbol, timeframe, expr)` (M2-2a extended grammar): pop 1 (the inner expr value),
    push 1 (always `series<float>` — decided by `typing/checker.py`'s `_infer_request`).
    `symbol`/`timeframe` were fixed as parse-time constants by M2-2a (the AST `RequestExpr`
    only accepts them as `str` fields, so a dynamic argument can never be assembled). MTF
    resampling (confirmed bars only, lookahead=off fixed) is handled by the runtime
    (DSL-8 `runtime/mtf.py`, M2-2b) — this instruction only carries the symbol/timeframe
    that evaluation needs."""

    op: Literal["request"] = "request"
    symbol: str
    timeframe: str
    type: Type


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
    | Order
    | Request,
    Field(discriminator="op"),
]

_DECL_OPS: Final = frozenset({"declare_input", "store", "plot", "signal", "order"})


class IRProgram(IRNode):
    ir_version: Literal["aios-ir-1"] = IR_VERSION
    grammar_version: Literal["aios-script-1"] = GRAMMAR_VERSION
    instrs: tuple[Instr, ...] = ()


for _cls in (Plot, Order, IRProgram):
    _cls.model_rebuild()


# ---- Serialization (determinism) ----


def to_bytes(ir: IRProgram) -> bytes:
    """IR → normalized JSON bytes. The same IR always yields the same bytes,
    regardless of when or where it is called.

    `sort_keys` fixes key order, the fixed separators fix whitespace, and
    `ensure_ascii` fixes unicode escaping. `allow_nan=False` is a second line
    of defense on top of `ConstFloat` validation (fail-closed with an
    exception if a non-standard token slips through).
    """
    return json.dumps(
        ir.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def from_bytes(data: bytes) -> IRProgram:
    """Inverse of `to_bytes`. Rejects version mismatches, unknown fields, and
    non-finite constants."""
    return IRProgram.model_validate(json.loads(data.decode("utf-8")))


# ---- Stack discipline verification ----


class IRStackError(Exception):
    """An IR instruction stream violates stack discipline (underflow or
    leftover value after a decl) — malformed IR."""


def stack_effect(instr: Instr) -> tuple[int, int]:
    """(pop count, push count) for an instruction. The single definition
    shared by the interpreter and the verifier."""
    if isinstance(instr, ConstInt | ConstFloat | Load):
        return (0, 1)
    if isinstance(instr, Neg | Not | Index | Request):
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
    """Walk the instruction stream once to confirm (1) no underflow, (2) the stack
    is empty right after each decl instruction, and (3) the stack is empty at the
    end. Violations raise `IRStackError`.

    `lower_program` calls this for every artifact, but it is also exposed publicly
    to re-verify an IR restored from bytes (`from_bytes`) before execution
    (I-07: failures must actually be raisable).
    """
    depth = 0
    for pos, instr in enumerate(ir.instrs):
        pops, pushes = stack_effect(instr)
        if depth < pops:
            raise IRStackError(f"#{pos} {instr.op}: 스택 언더플로(필요 {pops}, 현재 {depth})")
        depth = depth - pops + pushes
        if instr.op in _DECL_OPS and depth != 0:
            raise IRStackError(f"#{pos} {instr.op}: decl 뒤 스택 잔여값 {depth}개")
    if depth != 0:
        raise IRStackError(f"명령열 끝 스택 잔여값 {depth}개")
