"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 표 87행/§9.4 DSL-7 —
DSL-1 AST(`Program`) → IR(`ops.IRProgram`) 로우어링.

입력은 DSL-3이 만든 `Program`과 DSL-4 타입검사 결과(`TypeEnv`)다. 로우어링은
언제나 `check_program`을 스스로 다시 돌려 환경을 얻는다 — 호출자가 넘긴
`env`는 그 결과와 같아야 하며 다르면 거부한다(fail-closed: 검사되지 않은
AST나 다른 프로그램의 환경으로 IR을 만들 수 없다). 재선언이 `SCRIPT_TYPE`으로
막히므로 "최종 환경"으로 모든 decl의 표현식을 다시 추론해도 decl 시점의
환경과 결과가 같다(전방 참조는 검사기가 이미 거부했다).

타입은 `typing.checker.infer_type`으로 노드마다 다시 묻는다(승격 규칙의
단일 출처를 DSL-4에 둔다). 트리 깊이만큼 중복 순회가 생기지만 스크립트
크기는 DSL-6 리소스 상한이 묶는다.

결정론: 순회 순서는 post-order·왼쪽 우선으로 고정이고, 산출물은 불변
pydantic 모델 + 튜플이라 같은 `Program` 값이면 같은 `IRProgram` 값 → 같은
`to_bytes()` 바이트다. 산출 직후 `verify_stack`으로 스택 규율을 자기검증한다.

미지원 노드(§3.3 밖 객체, AST 판별 union에 없는 타입)는 `ScriptLowerError`로
거부한다 — 조용히 건너뛰거나 임의 명령으로 대체하지 않는다.

에러 코드: §3.3 taxonomy(SYNTAX/TYPE/LOOKAHEAD/RESOURCE_LIMIT)에는 로우어링
항목이 없다 — 타입검사를 통과한 §3.3 AST는 항상 내려가야 하기 때문이다.
`ScriptLowerError`는 그 계약이 깨졌음(또는 비유한 상수)을 뜻하며, HTTP
매핑(400/500)은 `POST /scripts/compile`(DSL-12) 계약에서 정한다(미확정).
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
    """AST→IR 로우어링 실패(미지원 노드·비유한 상수·환경 불일치). 모듈 docstring 참조."""

    code = "SCRIPT_LOWER"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def lower_program(program: Program, env: Mapping[str, Type] | None = None) -> IRProgram:
    """`Program` → `IRProgram`. 타입 오류는 DSL-4 `ScriptTypeError`가 그대로 전파된다.

    `env`를 넘기면 `check_program(program)` 결과와 일치해야 한다(불일치 = 거부).
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
    """표현식 하나 → 값 1개를 스택에 남기는 명령열(post-order). 테스트·DSL-8용 공개 API."""
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


# ---- expr (post-order, 왼쪽 우선) ----


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
    if isinstance(value, bool):  # DSL-1 `int | float`에 bool이 섞여 들어온 경우 — 문법 밖
        raise ScriptLowerError("숫자 리터럴 자리에 bool 값이 있습니다")
    if isinstance(value, int):
        return ConstInt(value=value)
    try:
        return ConstFloat(value=value)
    except ValidationError as exc:
        raise ScriptLowerError(f"유한하지 않은 숫자 상수: {value!r}") from exc
