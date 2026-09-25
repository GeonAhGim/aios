"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-4 —
AIOS Script static type checker.

Accepts only a `Program` (DSL-1 AST) produced by DSL-3 (`grammar/parser.py`).
Walks `decl`s in source order, registers `input`/`let`/`signal` names in the
type environment, and validates the expressions of `let`/`plot`/`signal`/`order`
against the promotion lattice in `types.py` (DoD: series/scalar promotion and
rejection). Violations are rejected fail-closed as `ScriptTypeError`
(`SCRIPT_TYPE` from the §3.3 taxonomy).

Out of scope (intentionally not checked):
- No built-in identifiers. Names like `close`/`open` that refer to market data
  must be declared first via `input ... : series<float> = 0` — the §3.3 syntax
  does not reserve such names (the `close` usages in test files are parser
  syntax examples only, not a contract assumed by DSL-4).
- `OrderDecl.side`/`qty_expr`/`opts`, `PlotDecl.style` — per DSL-1 (`ast.py`),
  these fields were accepted only as generic `Expr` because "there is no
  dedicated production in §3.3", so their meaning against the 5-type-lattice
  categories is not yet defined. We do not invent meaning here and only
  validate `when`/`expr`.

Unverified: per-function signatures for `ns.ident(...)` calls (`ta.*`/`math.*`/
`series.*`) cannot be determined yet because the IND registry
(`builtins_ta.py` etc. that DSL-9 will consume) does not exist. Until then,
the general rule applies: all arguments must be numeric, and if any argument
is a series the result is promoted to series (§3.3 "series/scalar promotion")
— per-function return types (e.g. `series.rising` possibly being `series<bool>`)
can be narrowed to just this function when the registry lands.

Errors carry no (line, col) — the DSL-1 `ScriptNode` has no position field
(the parser/AST belongs to another worker this cycle, so we do not extend it
ad hoc; see decision). §3.3 "position info included" is the final response
contract for `POST /scripts/compile` (DSL-12), and we do not pre-empt how that
leaf restores (line, col) mappings here.
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
    RequestExpr,
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
    """§3.3 error taxonomy `SCRIPT_TYPE`(400, non-retryable)."""

    code = "SCRIPT_TYPE"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def check_program(program: Program) -> TypeEnv:
    """Check `Program` decls in order and return the final type environment.

    Declaration order is the reference order (no forward references — §3.3
    syntax only allows sequential decl construction from top to bottom).
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
    """Infer the static type of an Expr. Raises `ScriptTypeError` on violation."""
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
    if isinstance(expr, RequestExpr):
        return _infer_request(expr, env)
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


def _infer_request(expr: RequestExpr, env: TypeEnv) -> Type:
    """M2-2a: request(symbol, timeframe, expr) always returns `series<float>` —
    it is a value materialised per-bar in another timeframe context and does not
    collapse to scalar (§3.3 promotion rules do not apply; this is a fixed rule).
    The inner `expr` must be numeric in the current env (rebinding in MTF
    context is M2-2b's responsibility)."""
    inner = infer_type(expr.expr, env)
    if inner not in NUMERIC_TYPES:
        raise ScriptTypeError(
            f"request(...)의 expr 인자는 수치 계열이어야 합니다(받음: {inner})"
        )
    return "series<float>"


def _infer_call(expr: CallExpr, env: TypeEnv) -> Type:
    arg_types = [infer_type(arg, env) for arg in expr.args]
    for i, t in enumerate(arg_types):
        if t not in NUMERIC_TYPES:
            raise ScriptTypeError(
                f"{expr.ns}.{expr.ident}()의 {i + 1}번째 인자는 수치 계열이어야 합니다"
                f"(받음: {t})"
            )
    return "series<float>" if any(is_series(t) for t in arg_types) else "float"
