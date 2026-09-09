"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-15 —
Pine AST(DSL-14 `PineProgram`) → AIOS Script AST(DSL-1 `Program`) 변환 +
컴파일·검증 왕복.

DSL-14가 파싱한 `PineProgram`만 입력으로 받는다(지원/미지원 판정은 파서가
이미 끝냈다). 문장 하나가 항상 decl 하나로 내려가지는 않는다 —
`AssignStmt(name, input.<kind>(...))`만 예외로 `InputDecl`(AIOS Script는
input이 최상위 decl이지 `let`에 얹는 개념이 아님)이 되고, 나머지 `AssignStmt`는
전부 `LetDecl`이다.

구조적으로 옮길 수 없는 것(전부 `PineTranspileError`, 사유를 메시지에 명시
— "의미 차이 명시" DoD): 문자열·원시 bool 리터럴 표현식(`grammar.ast.Expr`
판별 union에 둘 다 없음, DSL-1 decision — `input.bool(...)` 기본값 자리만
예외, 그 필드는 `Expr`가 아니라 `int|float|bool` 리터럴); `%`·`!=` 연산자
(`BinaryOp` 문법표 밖); 최상위가 아닌 자리의 `plot(...)`(`CallExpr.ns`가
필수라 네임스페이스 없는 호출을 실을 자리가 없음); `strategy.exit(...)`
(AIOS `OrderDecl`은 "신규 주문 하나"만 표현해 id로 기존 포지션을 찾아 닫는
"청산" 의미가 없음 — 억지로 넣으면 오역이라 거부); `ta.*`/`input.*` 호출의
키워드 인자(`CallExpr.args`는 위치 튜플뿐, `strategy.entry`의 `qty=`만 예외).

의미가 달라지는데도 변환하는 것: `plot(expr, ...)`의 스타일 인자(2번째부터)는
버린다(`PlotDecl.style`은 DSL-4/6이 검사 안 하는 미정의 필드라 대개
문자열·색상이라 옮길 자리도 없음). `strategy.entry(id, direction, qty=..)`의
`id`는 버리고, `qty` 생략 시 `1`로 고정한다(이 하위 문법엔 전략 선언이 없어
Pine의 `default_qty`가 없음). Pine의 `strategy.entry`는 보통 `if` 안에서만
실행되지만 이 Pine 하위 문법은 `if`가 없어(DSL-14) 파싱된 호출은 전부 무조건
실행문이다 — `OrderDecl.when`엔 "항상 참" 자리표시(`1 == 1`, 리터럴 `true`는
문법에 없음)를 채운다. OHLCV 식별자(open/high/low/close/volume)를 선언 없이
참조하면 `input <name> : series<float> = 0`을 자동 주입한다(진짜 입력이
아니라 시장데이터 참조지만 AIOS Script v1엔 그 별도 decl이 없음, 값은 안 쓰임).

lookahead(DSL-5) 재검증은 하지 않는다: Pine 파서가 이미 음수·변수 postfix
인덱스와 `request.*`/`security`류 네임스페이스를 전부 거부해서, 이 모듈이
만들 수 있는 `Program`은 애초에 `SCRIPT_LOOKAHEAD` 위반을 구성할 수 없다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.core.script.analysis.resources import DEFAULT_LIMITS, ResourceLimits, check_resources
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
    """Pine AST → AIOS Script AST 변환 불가(구조적으로 표현할 자리가 없음). (line, col)
    없음 — DSL-14 `import_/pine/ast.py`에 위치 필드가 없다(그 리프의 decision과 동일)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class TranspileResult:
    """변환 + 왕복 검증(타입·자원·IR 하강·직렬화)까지 통과한 산출물."""

    program: Program
    ir: IRProgram
    ir_bytes: bytes


def transpile_source(source: str) -> Program:
    """Pine 소스 → `parse`(DSL-14) → `transpile_program`."""
    return transpile_program(parse_pine(source))


def transpile_program(pine: pine_ast.PineProgram) -> Program:
    """`PineProgram`의 문장을 순서대로 `Decl`로 옮긴다."""
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
    """Pine 소스 → `Program` → 타입검사(DSL-4) → 자원상한(DSL-6) → IR 하강(DSL-7) →
    직렬화 왕복(`to_bytes`/`from_bytes` + `verify_stack`)까지 통과해야 성공한다("컴파일·
    검증 왕복", DoD). 실패는 `PineTranspileError` 또는 각 단계의 taxonomy 예외를 그대로
    전파한다(여기서 감싸지 않음 — §3.3 taxonomy는 소스 텍스트를 받는 DSL-12 API 계약이라
    AST를 받는 이 함수는 그 타입 밖이다)."""
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
            op=expr.op,  # type: ignore[arg-type]
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
