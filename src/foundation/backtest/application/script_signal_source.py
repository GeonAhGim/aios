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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from src.core.script.ir.ops import DeclareInput, IRProgram
from src.core.script.runtime.builtins_strategy import StrategyBuiltins
from src.core.script.runtime.builtins_ta import default_builtins
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.series import Series, Value
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    BracketMetadata,
    PositionState,
    SignalSource,
)
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent
from src.foundation.backtest.application.script_signal_plan import (
    ScriptSignalSourceError,
    _materialize_plan,
)
from src.foundation.market_data.api import CandleColumns
from src.foundation.research_data.api import research_builtins
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = ["ScriptSignalSourceError", "build_script_signal_source"]


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

    BT-10b (task-5195): `strategy.*` calls (task-5194 wired the parser) are now
    merged into the execution path via `StrategyBuiltins`. After execution, both
    `ExecutionResult.orders` (DSL `order()` declarations) and
    `StrategyBuiltins.intents` are consumed to materialize a combined plan. Since
    this DSL version has no per-bar conditionals guarding `strategy.*` calls, all
    strategy intents are mapped to bar 0 by design — a future DSL version with
    conditional support can associate intents with individual bars.

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
    strategy_builtins = StrategyBuiltins()
    builtins = dict(default_builtins())
    builtins.update(strategy_builtins.table)
    if research_instrument is not None:
        builtins.update(
            research_builtins(research_items, columns, instrument=research_instrument)
        )
    result = execute(ir, bar_count=bar_count, inputs=merged_inputs, builtins=builtins)
    plan, bracket = _materialize_plan(result, bar_count, strategy_builtins.intents)
    return _MaterializedSignalSource(plan, bracket)


@dataclass(frozen=True, slots=True)
class _MaterializedSignalSource:
    plan: Mapping[int, OrderIntent]
    bracket: BracketMetadata | None

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

