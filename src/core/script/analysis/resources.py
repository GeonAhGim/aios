"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-6 —
AIOS Script 컴파일 시 자원 산정치 상한 검사기.

Spec: §2 표 86행(`analysis/resources.py`, "루프·시리즈 길이·호출 깊이 상한
산정", 160줄), §3.3 169행("최대 시리즈 길이·연산 수·호출 깊이는 컴파일
산정치로 거부(`SCRIPT_RESOURCE_LIMIT`)"), §9.4 DoD("산정치 상한 거부").

DSL-4(`typing/checker.py`)가 통과시킨 `Program`(DSL-1 AST)만 입력으로
받는다 — 시리즈 개수를 세려면 타입 정보가 필요해서다. DSL-4의 `infer_type`
을 그대로 재사용하고(타입 추론 재구현 금지), 에러 관례(위치 없음, `code`
클래스 속성, fail-closed)도 DSL-4/5와 동일하게 따른다. 새 taxonomy 없음:
§3.3 4종 중 `SCRIPT_RESOURCE_LIMIT` 하나만 낸다.

산정 항목(전부 정적·결정론 — 스크립트 실행·I/O·DB 없음):
- series_count   : 시리즈로 구체화되는 decl 수(시리즈 타입 input/let/signal
                   + 시리즈를 그리는 plot). 런타임 시리즈 버퍼 개수의 상한.
- lookback_total : 모든 `[n]` 오프셋 n의 합 + 모든 호출의 기간 인자 합.
                   호출의 기간은 "정적으로 접히는 정수 인자 중 최댓값"으로
                   본다(§미검증 참조).
- op_count       : 표현식 노드 총수(§3.3 "연산 수").
- call_count     : `ns.ident(...)` 호출 총수(지표 호출 수).
- call_depth     : 호출 인자 안에 호출이 중첩된 최대 깊이(§3.3 "호출 깊이").
- plot_count     : plot decl 수.

Fail-closed(산정 불가 = 거부): 타입 추론이 실패하는 AST(DSL-4를 거치지
않았거나 env가 어긋남), 음수 기간 인자, bool 리터럴로 선언된 int input을
기간으로 쓰는 경우처럼 lookback을 정수로 확정할 수 없으면 "모르니 통과"가
아니라 `SCRIPT_RESOURCE_LIMIT`로 거부한다(DSL-5 decision과 동일 원칙).

상한 근거(코드 상수, `DEFAULT_LIMITS`):
- max_lookback_total 5000 = 지표 레지스트리 파라미터 상한(`specs_talib.py`
  `_MAX_PERIOD = 500`) × 10. 최대 기간 지표 10개를 직렬로 이어도 통과한다.
- max_series 64·max_plots 32: 차트 한 장에 겹치는 오버레이·페인 수의
  실용 상한(CH-3 overlayRegistry가 페인/오버레이를 개별 관리하는 규모).
- max_ops 2000·max_calls 100·max_call_depth 8: §3.3 문법에 반복·재귀가
  없어 노드 수가 곧 봉당 연산 수다. 한 봉당 2000 노드·호출 100개면 DSL-12
  컴파일 ≤300ms·즉시 백테스트 예산 안에 넉넉히 든다. 깊이 8은 사람이 읽을
  수 있는 중첩의 상한이자 인터프리터(DSL-8) 스택 보호선.

미검증: 호출별 실제 lookback은 IND 레지스트리(DSL-9 `builtins_ta.py`)가
붙어야 정확히 알 수 있다. 그때까지 "정적 정수 인자 최댓값"은 보수적
추정치이며, 기간 인자가 하나도 없는 호출(`math.abs(x)` 등)은 0으로 본다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.core.script.grammar.ast import (
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
    SignalDecl,
    UnaryExpr,
)
from src.core.script.typing.checker import ScriptTypeError, TypeEnv, check_program, infer_type
from src.core.script.typing.types import Type, is_series


class ScriptResourceLimitError(Exception):
    """§3.3 에러 taxonomy의 `SCRIPT_RESOURCE_LIMIT`(400, 재시도 불가).

    `metric`은 초과한 산정 항목 이름(산정 불가 거부면 `None`).
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


DEFAULT_LIMITS = ResourceLimits()


@dataclass(frozen=True)
class ResourceEstimate:
    series_count: int = 0
    lookback_total: int = 0
    op_count: int = 0
    call_count: int = 0
    call_depth: int = 0
    plot_count: int = 0


# (산정 항목, 상한 항목) — 검사 순서가 곧 오류 메시지 우선순위다.
_CHECKS: tuple[tuple[str, str], ...] = (
    ("series_count", "max_series"),
    ("lookback_total", "max_lookback_total"),
    ("op_count", "max_ops"),
    ("call_count", "max_calls"),
    ("call_depth", "max_call_depth"),
    ("plot_count", "max_plots"),
)


def check_resources(
    program: Program, limits: ResourceLimits = DEFAULT_LIMITS
) -> ResourceEstimate:
    """DSL-4 타입 검사를 다시 적용해 env를 얻은 뒤 산정·상한 검사한다.

    DSL-4를 통과하지 못하는 AST는 산정 불가이므로 `SCRIPT_RESOURCE_LIMIT`로
    거부한다(원인은 메시지에 남긴다). 통과하면 산정치를 돌려준다.
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
    """산정치가 상한을 하나라도 초과하면 `ScriptResourceLimitError`(경계 포함 통과)."""
    for metric, limit_name in _CHECKS:
        value: int = getattr(estimate, metric)
        limit: int = getattr(limits, limit_name)
        if value > limit:
            raise ScriptResourceLimitError(
                f"스크립트 자원 산정치 {metric}={value}가 상한 {limit_name}={limit}을 초과합니다",
                metric=metric,
            )


def estimate_resources(program: Program, env: TypeEnv) -> ResourceEstimate:
    """`Program`의 정적 자원 산정치. `env`는 DSL-4 `check_program`의 결과.

    env가 program과 어긋나면(타입 추론 실패) 산정 불가로 거부한다.
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
        else:  # pragma: no cover — Decl union은 닫혀 있다
            raise ScriptResourceLimitError(f"자원 산정 불가: 알 수 없는 decl {decl!r}")
    return ResourceEstimate(
        series_count=acc.series,
        lookback_total=acc.lookback,
        op_count=acc.ops,
        call_count=acc.calls,
        call_depth=acc.call_depth,
        plot_count=acc.plots,
    )


class _Acc:
    """단일 순회용 누산기(모듈 외부 비공개)."""

    def __init__(self, env: TypeEnv, inputs: dict[str, InputDecl]) -> None:
        self.env = env
        self.inputs = inputs
        self.series = 0
        self.lookback = 0
        self.ops = 0
        self.calls = 0
        self.call_depth = 0
        self.plots = 0

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
        raise ScriptResourceLimitError(  # pragma: no cover — Expr union은 닫혀 있다
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
        """정적으로 접히는 정수 인자만 값으로, 그 외(float·시리즈·let)는 None."""
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
            return None  # let으로 묶인 int — 정적 접힘 대상 아님(0으로 봄, §미검증)
        if isinstance(decl.value, bool) or not isinstance(decl.value, int):
            raise ScriptResourceLimitError(
                f"자원 산정 불가: int input {name!r}의 기본값 {decl.value!r}이"
                " 정수가 아니라 lookback을 확정할 수 없습니다"
            )
        return decl.value
