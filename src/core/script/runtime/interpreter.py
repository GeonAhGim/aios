"""L4_analytics_authoring_backtest_marketplace_v1.0.md §2.4 row 88 / §9.4 DSL-8 —
stack interpreter for the AIOS Script IR (DSL-7 `IRProgram`). Pure, no I/O, no recursion.

The execution model is "whole-bar vectorized". The instruction stream is walked
front to back exactly once (no jumps, no loops, no recursion — the IR has no
such instructions), and stack values are `Value` from `runtime/series.py`
(a scalar, or a series of length `bar_count`). Because nothing re-runs per bar,
`s[n]` becomes a shift, and arithmetic/comparison/logical ops become broadcast
elementwise ops. The same IR, same inputs, same registry always produce the
same result (backtest and live share output, I-05).

How IR type annotations are interpreted (this leaf's decision): an annotation
fixes the value's *domain* (int/float/bool) and a lower bound on "is a series".
If the annotation is `series<*>`, the runtime value must be a `Series`. Even a
scalar annotation (`float`/`bool`) may still hold a `Series` at runtime —
DSL-4 fixes the static type of `close[1]` as the element type `float`, but the
value varies per bar (so a script like `ta.sma(close[1], 3)` legitimately puts
that value back into a series slot). Only the `int` annotation is always a
scalar (indexing/calls never produce an int series). The interpreter does not
re-infer static types; it only checks that the annotation matches the actual
shape and domain at runtime (a mismatch is a `ScriptRuntimeError`, fail-closed).

Builtins (`ns.ident(...)`) dispatch only through the host-injected registry
(`BuiltinRegistry`). The bodies (`ta.*`/`math.*`/`strategy.*`) are owned by
DSL-9, so there is no stub or default here — an unregistered call raises.
Builtin return values are also checked against the annotation and bar count
(external code's output is not trusted). The DSL-9a builtin table is built by
`builtins_ta.default_builtins()` (`builtins_math.MATH_BUILTINS` +
`TaBuiltins.table`) and injected by the host — this module does not import the
indicator registry (keeping the no-I/O / no-external-dependency static check
intact), so `execute(builtins=None)` still gets an empty registry.

Operands with undefined semantics (`Order.side/qty_expr/opts`, `Plot.style`)
are carried through to the result exactly as the IR received them from the AST
— they are not evaluated. Fixing their semantics is DSL-11.

Inputs: series-typed inputs of `DeclareInput` (e.g. `close`) must always be
supplied by the host via `inputs` (a literal default of 0 is never broadcast
into a series). For scalar inputs, an `inputs` value takes precedence, falling
back to the declared literal otherwise. An undeclared name present in `inputs`
is an error (typos are never silently ignored).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final, cast

import src.core.script.runtime.mtf as mtf
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
    Request,
    Signal,
    Store,
    verify_stack,
)
from src.core.script.runtime.interpreter_types import (
    SCRIPT_RUNTIME_LIMIT,
    BuiltinRegistry,
    CallSite,
    ExecutionResult,
    OrderOutput,
    PlotOutput,
    ScriptRuntimeLimitError,
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
from src.core.script.typing.types import is_series

_ARITH: Final[frozenset[str]] = frozenset({"+", "-", "*", "/"})
_COMPARE: Final[frozenset[str]] = frozenset({"<", "<=", "==", ">=", ">"})
_CROSS: Final[frozenset[str]] = frozenset({"crosses_above", "crosses_below"})
_LOGICAL: Final[frozenset[str]] = frozenset({"and", "or"})


def execute(
    ir: IRProgram,
    *,
    bar_count: int,
    inputs: Mapping[str, Value] | None = None,
    builtins: BuiltinRegistry | None = None,
    symbol: str | None = None,
    base_timeframe: str | None = None,
    runtime_limit: int = SCRIPT_RUNTIME_LIMIT,
) -> ExecutionResult:
    """Execute the IR. Every failure is `ScriptRuntimeError` (or `IRStackError`
    if the IR itself is malformed).

    `symbol`/`base_timeframe` are only needed when the IR contains at least one
    `request(...)` (M2-2b `Request` instruction) — otherwise the default
    `None` is fine (backward compatible with existing callers). If
    `request(...)` is present but either one is missing, this raises
    `ScriptRuntimeError` (fail-closed, MTF evaluation is impossible)."""
    if isinstance(bar_count, bool) or not isinstance(bar_count, int) or bar_count < 0:
        raise ScriptRuntimeError(f"bar_count는 0 이상 정수여야 합니다: {bar_count!r}")
    verify_stack(ir)
    machine = _Machine(bar_count, dict(inputs or {}), builtins or {}, symbol, base_timeframe)
    machine.run(ir, runtime_limit=runtime_limit)
    return machine.result()


# ---- stack machine ----


class _Machine:
    def __init__(
        self,
        bar_count: int,
        inputs: dict[str, Value],
        builtins: BuiltinRegistry,
        symbol: str | None = None,
        base_timeframe: str | None = None,
    ):
        self._n = bar_count
        self._inputs = inputs
        self._builtins = builtins
        self._symbol = symbol
        self._base_timeframe = base_timeframe
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
            "request": self._request,
        }

    def run(self, ir: IRProgram, *, runtime_limit: int = SCRIPT_RUNTIME_LIMIT) -> None:
        declared = {i.name for i in ir.instrs if isinstance(i, DeclareInput)}
        unknown = sorted(set(self._inputs) - declared)
        if unknown:
            raise ScriptRuntimeError(f"선언되지 않은 입력 이름: {unknown}")
        for executed, instr in enumerate(ir.instrs, start=1):
            if executed > runtime_limit:  # SBX-1 budget, not DSL-6's static op-count cap
                raise ScriptRuntimeLimitError(f"#{executed} 실행 명령 수>{runtime_limit}")
            handler = self._ops.get(instr.op)
            if handler is None:
                raise ScriptRuntimeError(f"#{executed - 1} 알 수 없는 IR 명령: {instr.op!r}")
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

    # -- expressions --

    def _const(self, instr: Instr) -> None:
        assert isinstance(instr, ConstInt | ConstFloat)  # noqa: S101 — guaranteed by the dispatch key
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

    def _request(self, instr: Instr) -> None:
        """`request(symbol, timeframe, expr)` (M2-2b). The inner expr value is
        already on the stack (post-order) — validation and the closed-bar
        resample body live in `mtf.evaluate_request`."""
        assert isinstance(instr, Request)  # noqa: S101
        resampled = mtf.evaluate_request(
            self._pop(),
            bar_count=self._n,
            symbol=self._symbol,
            base_timeframe=self._base_timeframe,
            request_symbol=instr.symbol,
            request_timeframe=instr.timeframe,
        )
        self._stack.append(
            check_value(
                resampled, instr.type, self._n, f"request({instr.symbol!r}, {instr.timeframe!r})"
            )
        )

    # -- declarations --

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
        self._bind(instr.name, check_value(self._pop(), instr.type, self._n, f"let {instr.name}"))

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
