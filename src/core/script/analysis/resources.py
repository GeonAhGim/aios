"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-6 —
AIOS Script compile-time resource estimate ceiling checker.

Spec: §2 table row 86 (`analysis/resources.py`, "estimate loop/series-length/
call-depth ceilings", 160 lines), §3.3 line 169 ("max series length/operation
count/call depth are rejected by compile-time estimate
(`SCRIPT_RESOURCE_LIMIT`)"), §9.4 DoD ("estimate ceiling rejection").

Takes only a `Program` (DSL-1 AST) that has passed DSL-4 (`typing/checker.py`)
as input — counting series requires type information. Reuses DSL-4's
`infer_type` as-is (no reimplementing type inference), and follows the same
error conventions as DSL-4/5 (no location, `code` class attribute,
fail-closed). No new taxonomy: emits only `SCRIPT_RESOURCE_LIMIT`, one of
§3.3's 4 kinds.

Estimated items (all static and deterministic — no script execution, I/O, or
DB):
- series_count   : number of decls materialized as a series (series-typed
                   input/let/signal + plot that draws a series). Ceiling on
                   the runtime series buffer count.
- lookback_total : sum of every `[n]` offset n + sum of every call's period
                   argument. A call's period is taken as "the max among its
                   statically-foldable integer args" (see §unverified).
- op_count       : total expression node count (§3.3 "operation count").
- call_count     : total `ns.ident(...)` call count (indicator call count).
- call_depth     : max nesting depth of calls within call arguments (§3.3
                   "call depth").
- plot_count     : number of plot decls.
- request_count  : count of `request(symbol, timeframe, expr)` (M2-2a, an
                   extension outside §3.3) nodes. request(...) is always
                   type-inferred as series<float> (DSL-4), so it is already
                   reflected in the series_count of the decl holding it via
                   that path; it is not double-counted here, and instead only
                   gets its own separate ceiling via `max_requests` (see
                   §ceiling rationale).
- array_length   : total element count summed over every `array<float>`
                   literal (M2-3 step 2, task-8694). Unlike a series it is not
                   refreshed per bar -- it is a constant vector baked into the
                   compiled artifact -- but the interpreter (DSL-8) still
                   allocates this much memory once per compile, so it gets its
                   own limit.

Fail-closed (estimate not possible = reject): an AST whose type inference
fails (did not go through DSL-4, or env mismatch), a negative period
argument, or a case like using a bool-literal-declared int input as a period
where lookback cannot be pinned down to an integer — in these cases this
rejects with `SCRIPT_RESOURCE_LIMIT` rather than "unsure, so pass" (same
principle as the DSL-5 decision).

Ceiling rationale (code constants, `DEFAULT_LIMITS`):
- max_lookback_total 5000 = indicator registry parameter ceiling
  (`specs_talib.py` `_MAX_PERIOD = 500`) x 10. Passes even if 10 max-period
  indicators are chained in series.
- max_series 64 / max_plots 32: practical ceiling for the number of
  overlays/panes stacked on a single chart (the scale at which CH-3's
  overlayRegistry manages panes/overlays individually).
- max_ops 2000 / max_calls 100 / max_call_depth 8: §3.3's grammar has no
  loops or recursion, so node count is directly the per-bar operation count.
  2000 nodes / 100 calls per bar comfortably fits within DSL-12 compile
  <=300ms and the live-backtest budget. Depth 8 is both the limit of
  human-readable nesting and a stack-protection line for the interpreter
  (DSL-8).
- max_requests 8 (M2-2a): each `request(symbol, timeframe, expr)` must
  separately materialize an entire series buffer for a different
  symbol/timeframe, making it far heavier than an ordinary series binding —
  it gets its own ceiling at under 1/10 of `max_calls` (100). No measured
  MTF runtime cost exists until M2-2b lands, so this is a conservative
  default (§unverified).
- max_array_length 4096 (M2-3 step 2): an `array<float>` literal is fully
  baked into memory at compile time, so it is sized the same order of
  magnitude as `max_lookback_total` (5000), but gets its own constant because
  its accounting purpose differs from a series buffer (which grows with bar
  count) (unverified -- conservative default until the interpreter's actual
  cost is measured).

Unverified: the actual per-call lookback can only be known precisely once
the IND registry (DSL-9 `builtins_ta.py`) is wired in. Until then, "max of
the statically-folded integer args" is a conservative estimate, and a call
with no period argument at all (e.g. `math.abs(x)`) is treated as 0.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.script.grammar.ast import (
    ArrayLiteral,
    BinaryExpr,
    CallExpr,
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
from src.core.script.typing.checker import ScriptTypeError, TypeEnv, check_program, infer_type
from src.core.script.typing.types import Type, is_series


class ScriptResourceLimitError(Exception):
    """`SCRIPT_RESOURCE_LIMIT` from §3.3's error taxonomy (400, non-retryable).

    `metric` is the name of the estimated item that exceeded its ceiling
    (`None` if rejected because the estimate could not be computed).
    """

    code = "SCRIPT_RESOURCE_LIMIT"

    def __init__(self, message: str, metric: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.metric = metric


@dataclass(frozen=True)
class ResourceLimits:
    max_series: int = 64
    max_lookback_total: int = 5000
    max_ops: int = 2000
    max_calls: int = 100
    max_call_depth: int = 8
    max_plots: int = 32
    max_requests: int = 8
    max_array_length: int = 4096


DEFAULT_LIMITS = ResourceLimits()


@dataclass(frozen=True)
class ResourceEstimate:
    series_count: int = 0
    lookback_total: int = 0
    op_count: int = 0
    call_count: int = 0
    call_depth: int = 0
    plot_count: int = 0
    request_count: int = 0
    array_length: int = 0


# (estimated item, ceiling item) — check order is the error-message priority order.
_CHECKS: tuple[tuple[str, str], ...] = (
    ("series_count", "max_series"),
    ("lookback_total", "max_lookback_total"),
    ("op_count", "max_ops"),
    ("call_count", "max_calls"),
    ("call_depth", "max_call_depth"),
    ("plot_count", "max_plots"),
    ("request_count", "max_requests"),
    ("array_length", "max_array_length"),
)


def check_resources(program: Program, limits: ResourceLimits = DEFAULT_LIMITS) -> ResourceEstimate:
    """Re-run DSL-4 type checking to obtain env, then estimate and check ceilings.

    An AST that fails DSL-4 cannot be estimated, so it is rejected with
    `SCRIPT_RESOURCE_LIMIT` (the cause is kept in the message). On success,
    returns the estimate.
    """
    try:
        env = check_program(program)
    except ScriptTypeError as exc:
        raise ScriptResourceLimitError(
            f"자원 산정 불가: DSL-4 타입 검사를 통과하지 못한 AST입니다({exc.message})"
        ) from exc
    estimate = estimate_resources(program, env)
    enforce_limits(estimate, limits)
    return estimate


def enforce_limits(estimate: ResourceEstimate, limits: ResourceLimits) -> None:
    """`ScriptResourceLimitError` if any estimate exceeds its ceiling (boundary value passes)."""
    for metric, limit_name in _CHECKS:
        value: int = getattr(estimate, metric)
        limit: int = getattr(limits, limit_name)
        if value > limit:
            raise ScriptResourceLimitError(
                f"스크립트 자원 산정치 {metric}={value}가 상한 {limit_name}={limit}을 초과합니다",
                metric=metric,
            )


def estimate_resources(program: Program, env: TypeEnv) -> ResourceEstimate:
    """Static resource estimate for a `Program`. `env` is the result of DSL-4's `check_program`.

    If `env` is out of sync with `program` (type inference failure), rejects
    as not estimable.
    """
    inputs = {d.name: d for d in program.decls if isinstance(d, InputDecl)}
    acc = _Acc(env, inputs)
    for decl in program.decls:
        if isinstance(decl, InputDecl):
            acc.series += is_series(decl.type.name)
        elif isinstance(decl, LetDecl | SignalDecl):
            acc.series += is_series(acc.type_of(decl.expr))
            acc.visit(decl.expr)
        elif isinstance(decl, PlotDecl):
            acc.plots += 1
            acc.series += is_series(acc.type_of(decl.expr))
            acc.visit(decl.expr)
            if decl.style is not None:
                acc.visit(decl.style)
        elif isinstance(decl, OrderDecl):
            for expr in (decl.side, decl.qty_expr, decl.opts, decl.when):
                if expr is not None:
                    acc.visit(expr)
        else:  # pragma: no cover — the Decl union is closed
            raise ScriptResourceLimitError(f"자원 산정 불가: 알 수 없는 decl {decl!r}")
    return ResourceEstimate(
        series_count=acc.series,
        lookback_total=acc.lookback,
        op_count=acc.ops,
        call_count=acc.calls,
        call_depth=acc.call_depth,
        plot_count=acc.plots,
        request_count=acc.requests,
        array_length=acc.array_length,
    )


class _Acc:
    """Single-pass accumulator (private to this module)."""

    def __init__(self, env: TypeEnv, inputs: dict[str, InputDecl]) -> None:
        self.env = env
        self.inputs = inputs
        self.series = 0
        self.lookback = 0
        self.ops = 0
        self.calls = 0
        self.call_depth = 0
        self.plots = 0
        self.requests = 0
        self.array_length = 0

    def type_of(self, expr: Expr) -> Type:
        try:
            return infer_type(expr, self.env)
        except ScriptTypeError as exc:
            raise ScriptResourceLimitError(
                f"자원 산정 불가: 타입 환경과 어긋난 표현식입니다({exc.message})"
            ) from exc

    def visit(self, expr: Expr, depth: int = 0) -> None:
        self.ops += 1
        if isinstance(expr, NumberLiteral | Identifier):
            return
        if isinstance(expr, UnaryExpr | NotExpr):
            self.visit(expr.operand, depth)
            return
        if isinstance(expr, PostfixExpr):
            self.lookback += expr.index or 0
            self.visit(expr.base, depth)
            return
        if isinstance(expr, BinaryExpr):
            self.visit(expr.left, depth)
            self.visit(expr.right, depth)
            return
        if isinstance(expr, CallExpr):
            self.calls += 1
            self.call_depth = max(self.call_depth, depth + 1)
            self.lookback += self._call_period(expr)
            for arg in expr.args:
                self.visit(arg, depth + 1)
            return
        if isinstance(expr, ArrayLiteral):
            # array<float> literal: length contributes to the array_length
            # budget (constant vector materialised once at compile time, not
            # per-bar -- see module docstring's array_length item).
            self.array_length += len(expr.elements)
            for element in expr.elements:
                self.visit(element, depth)
            return
        if isinstance(expr, RequestExpr):
            # request(...) itself is always type-inferred as series<float>
            # (DSL-4 `_infer_request`), so the series_count of the parent
            # decl holding it is already reflected via that path -- here we
            # only count request_count separately (avoiding double
            # counting). The "weight" of a request gets its own separate
            # ceiling via request_count/max_requests (see §ceiling
            # rationale).
            self.requests += 1
            self.visit(expr.expr, depth)
            return
        raise ScriptResourceLimitError(  # pragma: no cover — the Expr union is closed
            f"자원 산정 불가: 알 수 없는 표현식 {expr!r}"
        )

    def _call_period(self, call: CallExpr) -> int:
        periods = [p for p in (self._static_int(a) for a in call.args) if p is not None]
        if not periods:
            return 0
        period = max(periods)
        if period < 0:
            raise ScriptResourceLimitError(
                f"자원 산정 불가: {call.ns}.{call.ident}()의 기간 인자가 음수({period})입니다"
            )
        return period

    def _static_int(self, expr: Expr) -> int | None:
        """An integer arg that statically folds to a value; otherwise (float/series/let) None."""
        if isinstance(expr, NumberLiteral):
            return expr.value if isinstance(expr.value, int) else None
        if isinstance(expr, UnaryExpr):
            inner = self._static_int(expr.operand)
            return None if inner is None else -inner
        if isinstance(expr, Identifier) and self.env.get(expr.name) == "int":
            return self._input_default(expr.name)
        return None

    def _input_default(self, name: str) -> int | None:
        decl = self.inputs.get(name)
        if decl is None:
            return None  # int bound by a let -- not statically foldable (treated as 0, §unverified)
        if isinstance(decl.value, bool) or not isinstance(decl.value, int):
            raise ScriptResourceLimitError(
                f"자원 산정 불가: int input {name!r}의 기본값 {decl.value!r}이"
                " 정수가 아니라 lookback을 확정할 수 없습니다"
            )
        return decl.value
