"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 표 88행/§9.4 DSL-8 —
AIOS Script IR(DSL-7 `IRProgram`) 스택 인터프리터. 순수·I/O 없음·재귀 없음.

실행 모델은 "전 봉 벡터화"다. 명령열을 앞에서 뒤로 정확히 한 번 훑고(점프·
루프·재귀 없음 — IR에 그런 명령이 없다), 스택 값은 `runtime/series.py`의
`Value`(스칼라 또는 길이 `bar_count`의 시리즈)다. 봉마다 다시 실행하지 않으므로
`s[n]`은 시프트, 산술·비교·논리는 브로드캐스트 원소 연산이 된다. 같은 IR·같은
입력·같은 레지스트리면 결과는 항상 같다(백테스트=라이브 산출물 공유, I-05).

IR 타입 주석의 해석(이 리프의 결정): 주석은 값의 *도메인*(int/float/bool)과
"시리즈임"의 하한을 고정한다. 주석이 `series<*>`면 런타임 값은 반드시
`Series`다. 주석이 스칼라(`float`/`bool`)여도 런타임 값은 `Series`일 수 있다 —
DSL-4가 `close[1]`의 정적 타입을 원소 타입 `float`로 정했지만 그 값은 봉마다
다르기 때문이다(`ta.sma(close[1], 3)`처럼 그 값을 다시 시리즈 자리에 넣는
스크립트가 정당하다). `int` 주석만은 항상 스칼라다(인덱싱·호출은 int를 내지
않는다). 인터프리터는 정적 타입을 다시 추론하지 않고 주석과 실제 모양·도메인의
정합만 검사한다(불일치 = `ScriptRuntimeError`, fail-closed).

빌트인(`ns.ident(...)`)은 호스트가 주입하는 레지스트리(`BuiltinRegistry`)로만
디스패치한다. 본체(`ta.*`/`math.*`/`strategy.*`)는 DSL-9 소유라 여기엔 스텁도
기본값도 없다 — 미등록 호출은 예외다. 빌트인 반환값도 주석·봉 수와 대조한다
(외부 코드의 산출물을 신뢰하지 않는다).

의미 미정의 피연산자(`Order.side/qty_expr/opts`, `Plot.style`)는 IR이 AST 원형으로
운반한 그대로 결과에 실어 보낸다 — 평가하지 않는다. 의미 확정은 DSL-11.

입력: `DeclareInput`의 시리즈 타입 입력(`close` 등)은 호스트가 `inputs`로 반드시
공급해야 한다(리터럴 기본값 0을 시리즈로 펴지 않는다). 스칼라 입력은 `inputs`
값이 우선, 없으면 선언 리터럴. 선언되지 않은 이름이 `inputs`에 있으면 오류
(오타를 조용히 무시하지 않는다).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol, cast

from src.core.script.grammar.ast import Expr
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
from src.core.script.runtime.series import (
    ArithOp,
    CompareOp,
    CrossOp,
    LogicalOp,
    ScriptRuntimeError,
    Value,
    arith,
    compare,
    cross,
    index,
    logical,
    logical_not,
    negate,
)
from src.core.script.runtime.values import check_value
from src.core.script.typing.types import Type, is_series

_ARITH: Final[frozenset[str]] = frozenset({"+", "-", "*", "/"})
_COMPARE: Final[frozenset[str]] = frozenset({"<", "<=", "==", ">=", ">"})
_CROSS: Final[frozenset[str]] = frozenset({"crosses_above", "crosses_below"})
_LOGICAL: Final[frozenset[str]] = frozenset({"and", "or"})


@dataclass(frozen=True)
class CallSite:
    """빌트인에 넘기는 호출 문맥. 반환값은 `result_type`·`bar_count`와 대조된다."""

    ns: str
    ident: str
    result_type: Type
    bar_count: int


class Builtin(Protocol):
    def __call__(self, args: tuple[Value, ...], site: CallSite) -> Value: ...


BuiltinRegistry = Mapping[tuple[str, str], Builtin]
"""(ns, ident) → 빌트인. DSL-9가 채운다. 인터프리터는 조회만 한다."""


@dataclass(frozen=True)
class PlotOutput:
    value: Value
    type: Type
    style: Expr | None


@dataclass(frozen=True)
class OrderOutput:
    when: Value
    side: Expr
    qty_expr: Expr
    opts: Expr | None


@dataclass(frozen=True)
class ExecutionResult:
    bar_count: int
    bindings: Mapping[str, Value]
    """input/let/signal 이름 전부(선언 순서)."""
    signals: Mapping[str, Value]
    plots: tuple[PlotOutput, ...]
    orders: tuple[OrderOutput, ...]


def execute(
    ir: IRProgram,
    *,
    bar_count: int,
    inputs: Mapping[str, Value] | None = None,
    builtins: BuiltinRegistry | None = None,
) -> ExecutionResult:
    """IR을 실행한다. 실패는 전부 `ScriptRuntimeError`(또는 IR 자체 결함이면 `IRStackError`)."""
    if isinstance(bar_count, bool) or not isinstance(bar_count, int) or bar_count < 0:
        raise ScriptRuntimeError(f"bar_count는 0 이상 정수여야 합니다: {bar_count!r}")
    verify_stack(ir)
    machine = _Machine(bar_count, dict(inputs or {}), builtins or {})
    machine.run(ir)
    return machine.result()


# ---- 스택 머신 ----


class _Machine:
    def __init__(self, bar_count: int, inputs: dict[str, Value], builtins: BuiltinRegistry):
        self._n = bar_count
        self._inputs = inputs
        self._builtins = builtins
        self._stack: list[Value] = []
        self._bindings: dict[str, Value] = {}
        self._signals: dict[str, Value] = {}
        self._plots: list[PlotOutput] = []
        self._orders: list[OrderOutput] = []
        self._ops: dict[str, Callable[[Instr], None]] = {
            "const_int": self._const,
            "const_float": self._const,
            "load": self._load,
            "neg": self._neg,
            "not": self._not,
            "index": self._index,
            "binop": self._binop,
            "call": self._call,
            "declare_input": self._declare_input,
            "store": self._store,
            "plot": self._plot,
            "signal": self._signal,
            "order": self._order,
        }

    def run(self, ir: IRProgram) -> None:
        declared = {i.name for i in ir.instrs if isinstance(i, DeclareInput)}
        unknown = sorted(set(self._inputs) - declared)
        if unknown:
            raise ScriptRuntimeError(f"선언되지 않은 입력 이름: {unknown}")
        for pos, instr in enumerate(ir.instrs):
            handler = self._ops.get(instr.op)
            if handler is None:
                raise ScriptRuntimeError(f"#{pos} 알 수 없는 IR 명령: {instr.op!r}")
            handler(instr)
        if self._stack:
            raise ScriptRuntimeError(f"실행 종료 시 스택 잔여값 {len(self._stack)}개")

    def result(self) -> ExecutionResult:
        return ExecutionResult(
            bar_count=self._n,
            bindings=dict(self._bindings),
            signals=dict(self._signals),
            plots=tuple(self._plots),
            orders=tuple(self._orders),
        )

    def _pop(self) -> Value:
        if not self._stack:
            raise ScriptRuntimeError("스택 언더플로")
        return self._stack.pop()

    def _bind(self, name: str, value: Value) -> None:
        if name in self._bindings:
            raise ScriptRuntimeError(f"이미 바인딩된 이름을 다시 바인딩했습니다: {name!r}")
        self._bindings[name] = value

    # -- 표현식 --

    def _const(self, instr: Instr) -> None:
        assert isinstance(instr, ConstInt | ConstFloat)  # noqa: S101 — 디스패치 키가 보장
        self._stack.append(instr.value)

    def _load(self, instr: Instr) -> None:
        assert isinstance(instr, Load)  # noqa: S101
        if instr.name not in self._bindings:
            raise ScriptRuntimeError(f"바인딩되지 않은 이름: {instr.name!r}")
        self._stack.append(self._bindings[instr.name])

    def _neg(self, instr: Instr) -> None:
        assert isinstance(instr, Neg)  # noqa: S101
        self._stack.append(negate(self._pop(), integer=instr.type == "int"))

    def _not(self, instr: Instr) -> None:
        assert isinstance(instr, Not)  # noqa: S101
        self._stack.append(logical_not(self._pop()))

    def _index(self, instr: Instr) -> None:
        assert isinstance(instr, Index)  # noqa: S101
        self._stack.append(index(self._pop(), instr.offset))

    def _binop(self, instr: Instr) -> None:
        assert isinstance(instr, BinOp)  # noqa: S101
        right, left = self._pop(), self._pop()
        op = instr.operator
        result: Value
        if op in _ARITH:
            result = arith(cast(ArithOp, op), left, right, integer=instr.type == "int")
        elif op in _COMPARE:
            result = compare(cast(CompareOp, op), left, right)
        elif op in _CROSS:
            result = cross(cast(CrossOp, op), left, right, bar_count=self._n)
        elif op in _LOGICAL:
            result = logical(cast(LogicalOp, op), left, right)
        else:
            raise ScriptRuntimeError(f"알 수 없는 이항 연산자: {op!r}")
        self._stack.append(result)

    def _call(self, instr: Instr) -> None:
        assert isinstance(instr, Call)  # noqa: S101
        key = (instr.ns, instr.ident)
        fn = self._builtins.get(key)
        if fn is None:
            raise ScriptRuntimeError(f"미등록 빌트인 호출: {instr.ns}.{instr.ident}")
        args = tuple(self._pop() for _ in range(instr.argc))[::-1]
        site = CallSite(instr.ns, instr.ident, instr.type, self._n)
        result = fn(args, site)
        self._stack.append(
            check_value(result, instr.type, self._n, f"{instr.ns}.{instr.ident}() 반환값")
        )

    # -- decl --

    def _declare_input(self, instr: Instr) -> None:
        assert isinstance(instr, DeclareInput)  # noqa: S101
        if instr.name in self._inputs:
            raw: Value = self._inputs[instr.name]
        elif is_series(instr.type):
            raise ScriptRuntimeError(
                f"시리즈 입력 {instr.name!r}({instr.type})은 호스트가 공급해야 합니다"
            )
        else:
            raw = instr.value
        self._bind(instr.name, check_value(raw, instr.type, self._n, f"input {instr.name}"))

    def _store(self, instr: Instr) -> None:
        assert isinstance(instr, Store)  # noqa: S101
        self._bind(
            instr.name, check_value(self._pop(), instr.type, self._n, f"let {instr.name}")
        )

    def _plot(self, instr: Instr) -> None:
        assert isinstance(instr, Plot)  # noqa: S101
        value = check_value(self._pop(), instr.type, self._n, "plot()")
        self._plots.append(PlotOutput(value=value, type=instr.type, style=instr.style))

    def _signal(self, instr: Instr) -> None:
        assert isinstance(instr, Signal)  # noqa: S101
        value = check_value(self._pop(), instr.type, self._n, f"signal {instr.name}")
        self._bind(instr.name, value)
        self._signals[instr.name] = value

    def _order(self, instr: Instr) -> None:
        assert isinstance(instr, Order)  # noqa: S101
        when = check_value(self._pop(), instr.when_type, self._n, "order() when")
        self._orders.append(
            OrderOutput(when=when, side=instr.side, qty_expr=instr.qty_expr, opts=instr.opts)
        )

