"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-4 —
AIOS Script 정적 타입 검사기.

DSL-3(`grammar/parser.py`)가 만든 `Program`(DSL-1 AST)만 입력으로 받는다.
decl을 소스 순서대로 훑으며 `input`/`let`/`signal` 이름을 타입 환경에
등록하고, `let`/`plot`/`signal`/`order`의 표현식을 `types.py`의 승격 격자로
검사한다(DoD: 시리즈/스칼라 승격·거부). 위반은 `ScriptTypeError`
(§3.3 taxonomy의 `SCRIPT_TYPE`)로 fail-closed 거부한다.

범위 밖(의도적으로 검사하지 않음):
- 내장 식별자 없음. `close`/`open`처럼 시장 데이터를 가리키는 이름도
  `input ... : series<float> = 0`으로 먼저 선언해야 참조할 수 있다 —
  §3.3 문법은 그런 이름을 예약하지 않는다(테스트 파일들의 `close` 등은
  파서 문법 예시일 뿐, DSL-4가 전제하는 계약이 아니다).
- `OrderDecl.side`/`qty_expr`/`opts`, `PlotDecl.style` — DSL-1(`ast.py`)
  자신의 decision대로 "§3.3에 별도 프로덕션이 없어 일반 Expr로만 받은"
  필드라 5종 타입 격자에 대응하는 의미가 아직 정의돼 있지 않다. 여기서
  임의로 의미를 만들어 붙이지 않고 `when`/`expr`만 검사한다.

미검증: `ns.ident(...)` 호출(`ta.*`/`math.*`/`series.*`)의 함수별 시그니처는
IND 레지스트리(DSL-9가 소비할 `builtins_ta.py` 등)가 아직 없어 알 수 없다.
그때까지는 인자가 전부 수치 계열이어야 하고, 하나라도 시리즈면 결과가
시리즈로 승격된다(§3.3 "시리즈/스칼라 승격")는 일반 규칙만 적용한다 —
함수별 반환 타입(예: `series.rising`이 series<bool>일 수 있는 경우)은
레지스트리가 붙는 시점에 이 함수만 좁히면 된다.

에러에는 (line, col)이 없다 — DSL-1 `ScriptNode`에 위치 필드가 없어서다
(파서·AST는 이번 사이클 다른 worker 소유라 임의로 확장하지 않는다, decision
참조). §3.3 "위치 정보 포함"은 `POST /scripts/compile`(DSL-12)의 최종
응답 계약이라, 그 리프가 (line, col) 매핑을 어떻게 복원할지는 여기서
선취하지 않는다.
"""
from __future__ import annotations

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
from src.core.script.typing.types import (
    BOOL_TYPES,
    NUMERIC_TYPES,
    Type,
    cmp_result,
    element_type,
    is_series,
    promote_bool,
    promote_numeric,
)

_CMP_AND_CROSS_OPS = frozenset(
    {"<", "<=", "==", ">=", ">", "crosses_above", "crosses_below"}
)
_ARITH_TERM_OPS = frozenset({"+", "-", "*", "/"})
_LOGICAL_OPS = frozenset({"or", "and"})

TypeEnv = dict[str, Type]


class ScriptTypeError(Exception):
    """§3.3 에러 taxonomy의 `SCRIPT_TYPE`(400, 재시도 불가)."""

    code = "SCRIPT_TYPE"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def check_program(program: Program) -> TypeEnv:
    """`Program`의 decl을 순서대로 검사하고 최종 타입 환경을 반환한다.

    선언 순서가 곧 참조 가능 순서다(전방 참조 없음 — §3.3 문법이 애초에
    decl을 앞에서부터 순차적으로만 구성하게 한다).
    """
    env: TypeEnv = {}
    for decl in program.decls:
        _check_decl(decl, env)
    return env


def _declare(env: TypeEnv, name: str, type_: Type) -> None:
    if name in env:
        raise ScriptTypeError(f"이미 선언된 식별자를 다시 선언했습니다: {name!r}")
    env[name] = type_


def _check_decl(decl: Decl, env: TypeEnv) -> None:
    if isinstance(decl, InputDecl):
        _declare(env, decl.name, decl.type.name)
        return
    if isinstance(decl, LetDecl):
        _declare(env, decl.name, infer_type(decl.expr, env))
        return
    if isinstance(decl, PlotDecl):
        result = infer_type(decl.expr, env)
        if result not in NUMERIC_TYPES:
            raise ScriptTypeError(
                f"plot()은 수치 계열(int/float/series<float>)만 그릴 수 있습니다"
                f"(받음: {result})"
            )
        return
    if isinstance(decl, SignalDecl):
        result = infer_type(decl.expr, env)
        if result not in BOOL_TYPES:
            raise ScriptTypeError(
                f"signal 조건은 bool 계열(bool/series<bool>)이어야 합니다(받음: {result})"
            )
        _declare(env, decl.name, result)
        return
    if isinstance(decl, OrderDecl):
        result = infer_type(decl.when, env)
        if result not in BOOL_TYPES:
            raise ScriptTypeError(
                f"order(...) when 조건은 bool 계열(bool/series<bool>)이어야 합니다"
                f"(받음: {result})"
            )
        return
    raise AssertionError(f"알 수 없는 decl kind: {decl!r}")  # pragma: no cover


def infer_type(expr: Expr, env: TypeEnv) -> Type:
    """Expr의 정적 타입을 추론한다. 위반은 `ScriptTypeError`."""
    if isinstance(expr, NumberLiteral):
        return "int" if isinstance(expr.value, int) else "float"
    if isinstance(expr, Identifier):
        if expr.name not in env:
            raise ScriptTypeError(f"정의되지 않은 식별자입니다: {expr.name!r}")
        return env[expr.name]
    if isinstance(expr, UnaryExpr):
        operand = infer_type(expr.operand, env)
        if operand not in NUMERIC_TYPES:
            raise ScriptTypeError(f"단항 '-'는 수치 계열에만 적용됩니다(받음: {operand})")
        return operand
    if isinstance(expr, NotExpr):
        operand = infer_type(expr.operand, env)
        if operand not in BOOL_TYPES:
            raise ScriptTypeError(f"'not'은 bool 계열에만 적용됩니다(받음: {operand})")
        return operand
    if isinstance(expr, PostfixExpr):
        return _infer_postfix(expr, env)
    if isinstance(expr, BinaryExpr):
        return _infer_binary(expr, env)
    if isinstance(expr, CallExpr):
        return _infer_call(expr, env)
    raise AssertionError(f"알 수 없는 Expr kind: {expr!r}")  # pragma: no cover


def _infer_postfix(expr: PostfixExpr, env: TypeEnv) -> Type:
    base = infer_type(expr.base, env)
    if expr.index is None:
        return base
    if not is_series(base):
        raise ScriptTypeError(
            f"'[n]' 인덱싱은 시리즈(series<float>/series<bool>)에만 적용됩니다(받음: {base})"
        )
    return element_type(base)


def _infer_binary(expr: BinaryExpr, env: TypeEnv) -> Type:
    left = infer_type(expr.left, env)
    right = infer_type(expr.right, env)
    if expr.op in _LOGICAL_OPS:
        result = promote_bool(left, right)
        if result is None:
            raise ScriptTypeError(
                f"{expr.op!r}는 bool 계열 피연산자가 필요합니다(받음: {left}, {right})"
            )
        return result
    if expr.op in _CMP_AND_CROSS_OPS:
        cmp = cmp_result(left, right)
        if cmp is None:
            raise ScriptTypeError(
                f"{expr.op!r}는 수치 계열 피연산자가 필요합니다(받음: {left}, {right})"
            )
        return cmp
    if expr.op in _ARITH_TERM_OPS:
        arith = promote_numeric(left, right)
        if arith is None:
            raise ScriptTypeError(
                f"{expr.op!r}는 수치 계열 피연산자가 필요합니다(받음: {left}, {right})"
            )
        return arith
    raise AssertionError(f"알 수 없는 BinaryOp: {expr.op!r}")  # pragma: no cover


def _infer_call(expr: CallExpr, env: TypeEnv) -> Type:
    arg_types = [infer_type(arg, env) for arg in expr.args]
    for i, t in enumerate(arg_types):
        if t not in NUMERIC_TYPES:
            raise ScriptTypeError(
                f"{expr.ns}.{expr.ident}()의 {i + 1}번째 인자는 수치 계열이어야 합니다"
                f"(받음: {t})"
            )
    return "series<float>" if any(is_series(t) for t in arg_types) else "float"
