"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-15 —
Converts Pine AST (DSL-14 `PineProgram`) to AIOS Script AST (DSL-1
`Program`), plus a compile/verify round trip.

Takes only a `PineProgram` already parsed by DSL-14 as input (the
supported/unsupported decision is already final by the time the parser
finishes). One statement does not always become one decl — the only
exception is `AssignStmt(name, input.<kind>(...))`, which becomes an
`InputDecl` (in AIOS Script, input is a top-level decl, not something
layered on `let`); every other `AssignStmt` becomes a `LetDecl`.

Things that cannot be carried over structurally (all raise
`PineTranspileError` with the reason stated in the message — the "state the
semantic difference" DoD): string and raw bool literal expressions (neither
is in the `grammar.ast.Expr` discriminated union, DSL-1 decision — the only
exception is the `input.bool(...)` default-value slot, whose field is an
`int|float|bool` literal, not an `Expr`); the `%`/`!=` operators (outside the
`BinaryOp` grammar table); `plot(...)` in a non-top-level position
(`CallExpr.ns` is required, so there is no slot for a namespace-less call);
`strategy.exit(...)` (AIOS `OrderDecl` can only express "one new order" — it
has no "close" semantics that look up an existing position by id; forcing it
in would be a mistranslation, so it is rejected); keyword arguments to
`ta.*`/`input.*` calls (`CallExpr.args` is a positional tuple only, with the
sole exception of `strategy.entry`'s `qty=`).

Things converted even though the semantics change: for `plot(expr, ...)`,
the style arguments (from the 2nd onward) are dropped (`PlotDecl.style` is
an undefined field DSL-4/6 do not check, and is usually a string/color with
no slot to carry it anyway). For `strategy.entry(id, direction, qty=..)`,
`id` is dropped, and `qty` is pinned to `1` when omitted (this subgrammar
has no strategy declaration, so there is no Pine `default_qty`). Pine's
`strategy.entry` normally only executes inside an `if`, but this Pine
subgrammar has no `if` (DSL-14), so every parsed call is an unconditional
statement — `OrderDecl.when` is filled with an "always true" placeholder
(`1 == 1`; a literal `true` is not in the grammar). Referencing an OHLCV
identifier (open/high/low/close/volume) without declaring it auto-injects
`input <name> : series<float> = 0` (it is really a market-data reference,
not a genuine input, but AIOS Script v1 has no separate decl for that, and
the value is unused).

Lookahead (DSL-5) is not re-verified here: the Pine parser already rejects
negative/variable postfix indices and `request.*`/`security`-style
namespaces outright, so any `Program` this module can produce could not
constitute a `SCRIPT_LOOKAHEAD` violation in the first place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from src.core.script.analysis.resources import DEFAULT_LIMITS, ResourceLimits, check_resources
from src.core.script.grammar.ast import (
    BinaryExpr,
    BinaryOp,
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
    TypeName,
    TypeNode,
    UnaryExpr,
)
from src.core.script.import_.pine import ast as pine_ast
from src.core.script.import_.pine.parser import parse as parse_pine
from src.core.script.ir.lower import lower_program
from src.core.script.ir.ops import IRProgram, from_bytes, to_bytes, verify_stack
from src.core.script.typing.checker import check_program

_OHLCV_NAMES: frozenset[str] = frozenset({"open", "high", "low", "close", "volume"})
_INPUT_KIND_TYPES: dict[str, TypeName] = {"int": "int", "float": "float", "bool": "bool"}
_SUPPORTED_BINARY_OPS: frozenset[str] = frozenset(
    {"or", "and", "<", "<=", "==", ">=", ">", "+", "-", "*", "/"}
)
_ONE: Expr = NumberLiteral(value=1)
_DEFAULT_QTY: Expr = _ONE
_ALWAYS_TRUE: Expr = BinaryExpr(op="==", left=_ONE, right=_ONE)


class PineTranspileError(Exception):
    """Pine AST cannot be converted to AIOS Script AST (structurally no slot to
    express it). No (line, col) — DSL-14 `import_/pine/ast.py` has no position
    field (same decision as that leaf)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class TranspileResult:
    """Output of conversion plus the round-trip verification (type, resource,
    IR lowering, serialization)."""

    program: Program
    ir: IRProgram
    ir_bytes: bytes


def transpile_source(source: str) -> Program:
    """Pine source -> `parse` (DSL-14) -> `transpile_program`."""
    return transpile_program(parse_pine(source))


def transpile_program(pine: pine_ast.PineProgram) -> Program:
    """Moves `PineProgram`'s statements into `Decl`s in order."""
    decls: list[Decl] = []
    declared: set[str] = set()
    for stmt in pine.statements:
        for name in _undeclared_ohlcv_refs(stmt, declared):
            decls.append(InputDecl(name=name, type=TypeNode(name="series<float>"), value=0))
            declared.add(name)
        decl = _transpile_stmt(stmt)
        decls.append(decl)
        if isinstance(decl, InputDecl | LetDecl):
            declared.add(decl.name)
    return Program(decls=tuple(decls))


def transpile_and_verify(
    source: str, *, limits: ResourceLimits = DEFAULT_LIMITS
) -> TranspileResult:
    """Succeeds only if Pine source -> `Program` -> type check (DSL-4) ->
    resource limits (DSL-6) -> IR lowering (DSL-7) -> serialization round trip
    (`to_bytes`/`from_bytes` + `verify_stack`) all pass ("compile/verify round
    trip" DoD). Failures propagate `PineTranspileError` or each stage's
    taxonomy exception as-is (not wrapped here — §3.3 taxonomy is a DSL-12 API
    contract that takes source text, and this function, which takes an AST,
    is outside that type)."""
    program = transpile_program(parse_pine(source))
    check_program(program)
    check_resources(program, limits)
    ir = lower_program(program)
    ir_bytes = to_bytes(ir)
    verify_stack(from_bytes(ir_bytes))
    return TranspileResult(program=program, ir=ir, ir_bytes=ir_bytes)


def _transpile_stmt(stmt: pine_ast.Statement) -> Decl:
    if isinstance(stmt, pine_ast.AssignStmt):
        return _transpile_assign(stmt)
    if isinstance(stmt, pine_ast.ExprStmt):
        return _transpile_expr_stmt(stmt.expr)
    raise PineTranspileError(f"알 수 없는 Pine 문장 노드: {type(stmt).__name__}")


def _transpile_assign(stmt: pine_ast.AssignStmt) -> Decl:
    expr = stmt.expr
    if isinstance(expr, pine_ast.CallExpr) and expr.ns == "input":
        return _transpile_input(stmt.name, expr)
    return LetDecl(name=stmt.name, expr=_transpile_expr(expr))


def _transpile_input(name: str, call: pine_ast.CallExpr) -> InputDecl:
    type_name = _INPUT_KIND_TYPES.get(call.ident)
    if type_name is None:
        raise PineTranspileError(
            f"input.{call.ident}(...)는 변환 대상이 아닙니다(int/float/bool만 지원 — "
            "문자열·심볼·소스류는 AIOS Script InputDecl.value에 대응 리터럴 타입이 없음)"
        )
    if not call.args or call.args[0].name is not None:
        raise PineTranspileError(f"input.{call.ident}(...)의 첫 인자는 위치 인자여야 합니다")
    value = _literal_value(call.args[0].value, type_name)
    return InputDecl(name=name, type=TypeNode(name=type_name), value=value)


def _literal_value(expr: pine_ast.Expr, type_name: TypeName) -> int | float | bool:
    if type_name == "bool":
        if isinstance(expr, pine_ast.BoolLiteral):
            return expr.value
        raise PineTranspileError("input.bool(...) 기본값은 true/false 리터럴이어야 합니다")
    if not isinstance(expr, pine_ast.NumberLiteral) or isinstance(expr.value, bool):
        raise PineTranspileError(f"input.{type_name}(...) 기본값은 숫자 리터럴이어야 합니다")
    if type_name == "float":
        return float(expr.value)
    if isinstance(expr.value, int):
        return expr.value
    raise PineTranspileError("input.int(...) 기본값은 정수 리터럴이어야 합니다")


def _transpile_expr_stmt(expr: pine_ast.Expr) -> Decl:
    if isinstance(expr, pine_ast.CallExpr) and expr.ns is None and expr.ident == "plot":
        return _transpile_plot(expr)
    if isinstance(expr, pine_ast.CallExpr) and expr.ns == "strategy":
        return _transpile_strategy(expr)
    raise PineTranspileError(
        "부작용 없는 단독 표현식 문장은 대응하는 AIOS Script decl이 없어 변환하지 않습니다"
        f"(받음: {type(expr).__name__})"
    )


def _transpile_plot(call: pine_ast.CallExpr) -> PlotDecl:
    if not call.args:
        raise PineTranspileError("plot(...)에는 표현식 인자가 최소 1개 필요합니다")
    return PlotDecl(expr=_transpile_expr(call.args[0].value), style=None)


def _transpile_strategy(call: pine_ast.CallExpr) -> OrderDecl:
    if call.ident != "entry":
        raise PineTranspileError(
            "strategy.exit(...)는 id로 기존 포지션을 종료하는 의미라 AIOS Script "
            "order(...)(신규 주문 하나)로 옮기면 오역이 됩니다 — 변환하지 않습니다"
            "(§9.9 DSL-15 decision)"
        )
    positional = [arg.value for arg in call.args if arg.name is None]
    keyword = {arg.name: arg.value for arg in call.args if arg.name is not None}
    if len(positional) < 2:
        raise PineTranspileError(
            "strategy.entry(id, direction, ...)는 최소 id·direction 두 인자가 필요합니다"
        )
    side = _transpile_expr(positional[1])
    qty_source = keyword.get("qty", positional[2] if len(positional) > 2 else None)
    qty_expr = _transpile_expr(qty_source) if qty_source is not None else _DEFAULT_QTY
    return OrderDecl(side=side, qty_expr=qty_expr, opts=None, when=_ALWAYS_TRUE)


def _transpile_expr(expr: pine_ast.Expr) -> Expr:
    if isinstance(expr, pine_ast.NumberLiteral):
        return NumberLiteral(value=expr.value)
    if isinstance(expr, pine_ast.Identifier):
        return Identifier(name=expr.name)
    if isinstance(expr, pine_ast.UnaryExpr):
        return UnaryExpr(op="-", operand=_transpile_expr(expr.operand))
    if isinstance(expr, pine_ast.NotExpr):
        return NotExpr(operand=_transpile_expr(expr.operand))
    if isinstance(expr, pine_ast.PostfixExpr):
        return PostfixExpr(base=_transpile_expr(expr.base), index=expr.index)
    if isinstance(expr, pine_ast.BinaryExpr):
        if expr.op not in _SUPPORTED_BINARY_OPS:
            raise PineTranspileError(
                f"연산자 {expr.op!r}는 AIOS Script BinaryOp 문법표에 없어 변환하지 않습니다"
            )
        return BinaryExpr(
            op=cast(BinaryOp, expr.op),
            left=_transpile_expr(expr.left),
            right=_transpile_expr(expr.right),
        )
    if isinstance(expr, pine_ast.CallExpr):
        return _transpile_call(expr)
    if isinstance(expr, pine_ast.StringLiteral):
        raise PineTranspileError(
            "AIOS Script 문법에는 문자열 리터럴 표현식이 없어(DSL-1 decision) 변환할 수 "
            "없습니다"
        )
    if isinstance(expr, pine_ast.BoolLiteral):
        raise PineTranspileError(
            "AIOS Script 문법에는 원시 bool 리터럴 표현식이 없어(DSL-1 decision) 변환할 수 "
            "없습니다(input.bool(...) 기본값 자리에서만 변환 가능)"
        )
    raise PineTranspileError(f"알 수 없는 Pine 표현식 노드: {type(expr).__name__}")


def _transpile_call(expr: pine_ast.CallExpr) -> CallExpr:
    if expr.ns is None:
        raise PineTranspileError(
            f"{expr.ident}(...)처럼 네임스페이스 없는 호출은 최상위 plot() 문장 자리에서만 "
            "변환 가능합니다(AIOS Script CallExpr는 네임스페이스가 필수 필드)"
        )
    return CallExpr(ns=expr.ns, ident=expr.ident, args=tuple(_call_arg(a) for a in expr.args))


def _call_arg(arg: pine_ast.CallArg) -> Expr:
    if arg.name is not None:
        raise PineTranspileError(
            f"{arg.name}=...  형태의 키워드 인자는 AIOS Script CallExpr(위치 인자만 허용)에 "
            "대응 자리가 없어 변환하지 않습니다"
        )
    return _transpile_expr(arg.value)


def _undeclared_ohlcv_refs(stmt: pine_ast.Statement, declared: set[str]) -> tuple[str, ...]:
    names: set[str] = set()
    _collect_identifiers(stmt.expr, names)
    return tuple(sorted(n for n in names if n in _OHLCV_NAMES and n not in declared))


def _collect_identifiers(expr: pine_ast.Expr, out: set[str]) -> None:
    if isinstance(expr, pine_ast.Identifier):
        out.add(expr.name)
    elif isinstance(expr, pine_ast.UnaryExpr | pine_ast.NotExpr):
        _collect_identifiers(expr.operand, out)
    elif isinstance(expr, pine_ast.PostfixExpr):
        _collect_identifiers(expr.base, out)
    elif isinstance(expr, pine_ast.BinaryExpr):
        _collect_identifiers(expr.left, out)
        _collect_identifiers(expr.right, out)
    elif isinstance(expr, pine_ast.CallExpr):
        for arg in expr.args:
            _collect_identifiers(arg.value, out)


__all__ = [
    "PineTranspileError",
    "TranspileResult",
    "transpile_and_verify",
    "transpile_program",
    "transpile_source",
]
