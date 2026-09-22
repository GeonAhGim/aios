"""BT-10b — Compiled AIOS Script (DSL) IR to BT-10 SignalSource bridge.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-10 (caller entry point, task-1504 9a1ae87 `quick_backtest.py`), §9.4
DSL-7 (IR, task-1503) · DSL-8 (`interpreter.execute`, task-1522 9fa72b5) ·
DSL-12 (`compile_source`, task-1535 8c0c10e). BT-10b itself is a new leaf
not yet listed in this spec revision (task-1625 decision) — it is a distinct
consumer from DSL-11 `script_facade` (`src/core/strategy`,
FROZEN_PAPER_ONLY) and does not preempt or replace it.

To comply with I-05 (backtest and live share the same compilation output
and domain logic), this module does not re-implement
`interpreter.execute()`. It runs `bar_count` over the full range exactly
once, and pre-materialises the output
(`ExecutionResult.orders`) `OrderOutput.when`/`qty_expr`/`side` into
`OrderIntent` (quick_backtest contract) keyed by bar index. Since
`on_bar` only looks up this table by index, it never re-runs the
interpreter per bar (performance), and structurally cannot return values
for any index other than the current one (no look-ahead — there is no way
to fetch a different index at all).

`Order.side`/`qty_expr`/`opts` are intentionally carried as bare AST
nodes by DSL-1/4/7/8 without interpretation
(`runtime/interpreter.py` module docstring: "meaning resolved by DSL-11").
This bridge establishes new v1 rules as the backtest consumer (explicit
rejection over guessing, fail-closed):

- `side`: only `buy`/`sell` identifiers accepted (case-sensitive, no
  reserved words beyond these two names — `typing/checker.py` decision).
  Any other form/name is rejected.
- `qty_expr`: only constant literals or already-executed names
  (lookup in `ExecutionResult.bindings` — not a re-evaluation, just
  retrieving values `execute()` already computed) are accepted. Binary
  expressions, call expressions, etc. are rejected (no re-implementation).
- `opts`: the schema is not yet defined anywhere (DSL-11 responsibility,
  note: both `ir/ops.py` and `typing/checker.py` state "meaning
  undefined"). Only `None` is accepted (market orders); anything else is
  rejected — we do not fabricate `order_type`/`trigger_price` by guess.
- If two or more `order()` calls fire on the same bar, the priority is
  undefined, so we reject (fail-closed, no guessing).

Pure module — no I/O (TID251, backtest/application zone).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from src.core.script.grammar.ast import Expr, Identifier, NumberLiteral
from src.core.script.ir.ops import DeclareInput, IRProgram
from src.core.script.runtime.builtins_ta import default_builtins
from src.core.script.runtime.interpreter import ExecutionResult, execute
from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    PositionState,
    SignalSource,
)
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.research_data.adapters.dsl_query import research_builtins
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = ["ScriptSignalSourceError", "build_script_signal_source"]

_SIDE_BY_NAME: Mapping[str, OrderSide] = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}


class ScriptSignalSourceError(ScriptRuntimeError):
    """Raised when this bridge cannot safely interpret `order()` side/qty_expr/opts
    or when a materialised value violates the `OrderIntent` contract (fail-closed).
    Subclass of `ScriptRuntimeError` so callers can catch "script execution/interpretation
    failure" under one exception hierarchy."""


def build_script_signal_source(
    ir: IRProgram,
    *,
    bar_count: int,
    inputs: Mapping[str, Value] | None = None,
    columns: CandleColumns,
    research_items: Sequence[ResearchItem] = (),
    research_instrument: str | None = None,
) -> SignalSource:
    """Run `ir` exactly once over the full `bar_count` (= `len(columns)`) range,
    pre-compute the resulting orders per bar index, and return them. Subsequent
    `on_bar` calls are pure dictionary look-ups only (determinism/performance —
    the interpreter is never re-run per bar).

    RD-9: when `research_instrument` is given, the `research.*` namespace
    (`domain/dsl_query.research_builtins`) is merged into the builtin table so
    the script can call `research.filing_count()` etc., auto-bound per bar to
    that bar's `columns.ts[i]` (RD-A1 -- see `dsl_query.py` module docstring
    for why the call shape structurally cannot request a future `as_of`).
    `research_items` should already be the caller's point-in-time-agnostic
    fetch for `research_instrument` (this function does no I/O)."""
    if len(columns) != bar_count:
        raise ScriptSignalSourceError(
            f"columns length ({len(columns)}) differs from bar_count ({bar_count})"
        )
    merged_inputs: dict[str, Value] = {**_market_inputs(ir, columns), **dict(inputs or {})}
    builtins = dict(default_builtins())
    if research_instrument is not None:
        builtins.update(
            research_builtins(research_items, columns, instrument=research_instrument)
        )
    result = execute(ir, bar_count=bar_count, inputs=merged_inputs, builtins=builtins)
    plan = _materialize_plan(result, bar_count)
    return _MaterializedSignalSource(plan)


@dataclass(frozen=True, slots=True)
class _MaterializedSignalSource:
    plan: Mapping[int, OrderIntent]

    def on_bar(self, window: BarWindow, _position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


def _market_inputs(ir: IRProgram, columns: CandleColumns) -> dict[str, Value]:
    """`open`/`high`/`low`/`close`/`volume` are not reserved words
    (`typing/checker.py` decision) — populate `CandleColumns` series only
    when the script actually declared them via
    `input <name>: series<float> = 0`. Populating undeclared names in
    `inputs` would cause `execute` to reject with "undeclared input name",
    so we never fill them unconditionally."""
    declared_series = {
        instr.name
        for instr in ir.instrs
        if isinstance(instr, DeclareInput) and instr.type == "series<float>"
    }
    candidates: dict[str, list[Decimal]] = {
        "open": columns.open,
        "high": columns.high,
        "low": columns.low,
        "close": columns.close,
        "volume": columns.volume,
    }
    return {
        name: Series.of_floats(float(v) for v in values)
        for name, values in candidates.items()
        if name in declared_series
    }


def _materialize_plan(result: ExecutionResult, bar_count: int) -> Mapping[int, OrderIntent]:
    plan: dict[int, OrderIntent] = {}
    for order in result.orders:
        side = _resolve_side(order.side)
        if order.opts is not None:
            raise ScriptSignalSourceError(
                "order() opts meaning is not yet defined (DSL-11 responsibility) — "
                "this bridge rejects orders with opts"
            )
        qty_source = _resolve_qty_source(order.qty_expr, result.bindings)
        for i in range(bar_count):
            if _value_at(order.when, i, bar_count) is not True:
                continue
            if i in plan:
                raise ScriptSignalSourceError(
                    f"Multiple order() calls fired at bar {i} — priority is undefined"
                )
            plan[i] = OrderIntent(
                side=side,
                quantity=_to_quantity(_value_at(qty_source, i, bar_count)),
                order_type="market",
                trigger_price=None,
            )
    return MappingProxyType(plan)


def _resolve_side(expr: Expr) -> OrderSide:
    if isinstance(expr, Identifier) and expr.name in _SIDE_BY_NAME:
        return _SIDE_BY_NAME[expr.name]
    raise ScriptSignalSourceError(
        f"order() side only supports buy/sell identifiers (received: {expr!r})"
    )


def _resolve_qty_source(expr: Expr, bindings: Mapping[str, Value]) -> Value:
    if isinstance(expr, NumberLiteral):
        return expr.value
    if isinstance(expr, Identifier):
        if expr.name not in bindings:
            raise ScriptSignalSourceError(f"order() qty_expr name is not bound: {expr.name!r}")
        return bindings[expr.name]
    raise ScriptSignalSourceError(
        "order() qty_expr only supports constant literals or already-bound names — "
        f"this bridge does not create a second interpreter (received: {expr!r})"
    )


def _value_at(value: Value, bar: int, bar_count: int) -> Scalar:
    if isinstance(value, Series):
        if len(value) != bar_count:
            raise ScriptSignalSourceError(
                f"series length ({len(value)}) differs from bar count ({bar_count})"
            )
        return value.at(bar)
    return value


def _to_quantity(raw: Scalar) -> Decimal:
    if raw is None:
        raise ScriptSignalSourceError(
            "Order quantity is na — cannot determine quantity on the firing bar"
        )
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise ScriptSignalSourceError(f"Order quantity is not numeric: {raw!r}")
    if isinstance(raw, float) and not math.isfinite(raw):
        raise ScriptSignalSourceError(f"Order quantity is not a finite number: {raw!r}")
    quantity = Decimal(str(raw))
    if quantity.is_nan() or quantity <= 0:
        raise ScriptSignalSourceError(f"Order quantity must be positive: {quantity}")
    return quantity
