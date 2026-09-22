"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 table row 87 / §9.4 DSL-7 —

DSL-1 AST (`Program`) → IR (`ops.IRProgram`) lowering.

Inputs are the `Program` produced by DSL-3 and the type-check result
(`TypeEnv`) from DSL-4. Lowering always re-runs `check_program` itself to
obtain the environment — the caller-supplied `env` must match that result,
otherwise it is rejected (fail-closed: IR must not be built from an
un-checked AST or an environment belonging to a different program). Because
re-declarations are blocked by `SCRIPT_TYPE`, re-inferring every decl's
expression with the "final environment" yields the same result as at decl
time (forward references were already rejected by the checker).

Types are re-queried per-node via `typing.checker.infer_type` (the single
source of promotion rules lives in DSL-4). Traversal duplicates work equal
to tree depth, but script size is bounded by the DSL-6 resource cap.

Determinism: traversal order is fixed post-order, left-first; outputs are
immutable pydantic models + tuples, so the same `Program` value always
produces the same `IRProgram` value → the same `to_bytes()` bytes.
`verify_stack` self-verifies stack discipline immediately after production.

Unsupported nodes (objects outside §3.3, types not in the AST discriminant
union) are rejected via `ScriptLowerError` — never silently skipped or
replaced with a synthetic instruction.

Error codes: the §3.3 taxonomy (SYNTAX / TYPE / LOOKAHEAD / RESOURCE_LIMIT)
has no lowering entries — a §3.3 AST that passed type-check must always
lower. `ScriptLowerError` signals that this contract was broken (or an
unspecified constant was encountered); HTTP mapping (400 / 500) is defined
by the `POST /scripts/compile` (DSL-12) contract (TBD).
"""
from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from src.core.script.grammar.ast import (
    BinaryExpr,
    CallExpr,
    Decl,
    Expr,
    Identifier,
    InputDecl,
    LetDecl,
    NotExpr,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    PostfixExpr,
    Program,
    SignalDecl,
    UnaryExpr,
)
from src.core.script.ir.ops import (
    BinOp,
    Call,
    ConstFloat,
    ConstInt,
    DeclareInput,
    Index,
    Instr,
    IRProgram,
    Load,
    Neg,
    Not,
    Order,
    Plot,
    Signal,
    Store,
    verify_stack,
)
from src.core.script.typing.checker import TypeEnv, check_program, infer_type
from src.core.script.typing.types import Type


class ScriptLowerError(Exception):
    """AST→IR lowering failure (unsupported node, unspecified constant,
    env mismatch). See module docstring."""

    code = "SCRIPT_LOWER"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def lower_program(program: Program, env: Mapping[str, Type] | None = None) -> IRProgram:
    """`Program` → `IRProgram`. Type errors from DSL-4 `ScriptTypeError` propagate as-is.

    If `env` is provided it must match `check_program(program)` result (mismatch = reject).
    """
    if not isinstance(program, Program):
        raise ScriptLowerError(f"Program 노드가 아닙니다: {type(program).__name__}")
    checked = check_program(program)
    if env is not None and dict(env) != checked:
        raise ScriptLowerError("전달된 타입 환경이 이 프로그램의 타입검사 결과와 다릅니다")

    instrs: list[Instr] = []
    for decl in program.decls:
        _lower_decl(decl, checked, instrs)
    ir = IRProgram(grammar_version=program.grammar_version, instrs=tuple(instrs))
    verify_stack(ir)
    return ir


def lower_expr(expr: Expr, env: TypeEnv) -> tuple[Instr, ...]:
    """Single expression → instruction sequence leaving one value on
    the stack (post-order). Public API for tests / DSL-8."""
    out: list[Instr] = []
    _emit_expr(expr, env, out)
    return tuple(out)


# ---- decl ----


def _lower_decl(decl: Decl, env: TypeEnv, out: list[Instr]) -> None:
    if isinstance(decl, InputDecl):
        out.append(DeclareInput(name=decl.name, type=decl.type.name, value=decl.value))
    elif isinstance(decl, LetDecl):
        _emit_expr(decl.expr, env, out)
        out.append(Store(name=decl.name, type=infer_type(decl.expr, env)))
    elif isinstance(decl, PlotDecl):
        _emit_expr(decl.expr, env, out)
        out.append(Plot(type=infer_type(decl.expr, env), style=decl.style))
    elif isinstance(decl, SignalDecl):
        _emit_expr(decl.expr, env, out)
        out.append(Signal(name=decl.name, type=infer_type(decl.expr, env)))
    elif isinstance(decl, OrderDecl):
        _emit_expr(decl.when, env, out)
        out.append(
            Order(
                side=decl.side,
                qty_expr=decl.qty_expr,
                opts=decl.opts,
                when_type=infer_type(decl.when, env),
            )
        )
    else:
        raise ScriptLowerError(f"지원하지 않는 decl 노드: {type(decl).__name__}")


# ---- expr (post-order, left-first) ----


def _emit_expr(expr: Expr, env: TypeEnv, out: list[Instr]) -> None:
    if isinstance(expr, NumberLiteral):
        out.append(_const(expr))
    elif isinstance(expr, Identifier):
        out.append(Load(name=expr.name, type=infer_type(expr, env)))
    elif isinstance(expr, UnaryExpr):
        _emit_expr(expr.operand, env, out)
        out.append(Neg(type=infer_type(expr, env)))
    elif isinstance(expr, NotExpr):
        _emit_expr(expr.operand, env, out)
        out.append(Not(type=infer_type(expr, env)))
    elif isinstance(expr, PostfixExpr):
        _emit_expr(expr.base, env, out)
        if expr.index is not None:
            out.append(Index(offset=expr.index, type=infer_type(expr, env)))
    elif isinstance(expr, BinaryExpr):
        _emit_expr(expr.left, env, out)
        _emit_expr(expr.right, env, out)
        out.append(BinOp(operator=expr.op, type=infer_type(expr, env)))
    elif isinstance(expr, CallExpr):
        for arg in expr.args:
            _emit_expr(arg, env, out)
        out.append(
            Call(ns=expr.ns, ident=expr.ident, argc=len(expr.args), type=infer_type(expr, env))
        )
    else:
        raise ScriptLowerError(f"지원하지 않는 Expr 노드: {type(expr).__name__}")


def _const(literal: NumberLiteral) -> Instr:
    value = literal.value
    if isinstance(value, bool):  # bool leaked into DSL-1 `int | float` — outside grammar
        raise ScriptLowerError("숫자 리터럴 자리에 bool 값이 있습니다")
    if isinstance(value, int):
        return ConstInt(value=value)
    try:
        return ConstFloat(value=value)
    except ValidationError as exc:
        raise ScriptLowerError(f"유한하지 않은 숫자 상수: {value!r}") from exc
