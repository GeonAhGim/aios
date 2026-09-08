"""L4_analytics_authoring_backtest_marketplace_v1.0.md Sec 3.3/Sec 9.4 DSL-9(b) --
AIOS Script `strategy.*` builtins: order *intent* values only.

Scope (module table row 89, Sec 1.2): this leaf never submits an order, calls an
exchange, or touches an OMS. `src/core/strategy|portfolio|risk|executor` stay
FROZEN_PAPER_ONLY and untouched -- the one sanctioned facade into them is
DSL-11 `src/core/strategy/script_facade.py`, which this file is not. All this
module does is validate a `strategy.*` call's arguments and record an
in-memory intent; consuming that intent (submitting real orders, feeding a
backtest fill model, etc.) is entirely a later leaf's job.

Design decisions this leaf makes (the spec leaves them open):

- Numeric side encoding. Sec 3.3's `call := ns "." ident "(" args ")"` is typed
  generically by `typing/checker.py::_infer_call`: every argument must be
  NUMERIC_TYPES and the result is float/series<float>. There is no bool or
  string type anywhere in this grammar, so `side` cannot be a `buy`/`sell`
  identifier the way the dedicated `order(...) when expr` production allows
  (`grammar/ast.py::OrderDecl.side` is an unchecked bare `Expr`). This leaf
  encodes direction as a plain numeric constant instead: `1` = long, `-1` =
  short. Any other numeric value is rejected.
- Constant-only side/qty. `runtime/interpreter.py`'s module docstring already
  establishes that a statically "float"-typed value can still be a runtime
  `Series` (e.g. `close[1]`) because DSL-8 executes the whole bar range at
  once. `src/foundation/backtest/application/script_signal_source.py`
  (BT-10b, this leaf's eventual consumer) only accepts a constant side/qty
  per order -- this module enforces the identical rule at call time via
  `isinstance(value, Series)`, independent of what the compiler inferred
  statically.
- Position info. DSL-4/DSL-5 already documented (checker.py, lookahead.py)
  that the AST/IR carry no (line, col) -- `ScriptNode` has no position field,
  so nothing downstream of the parser can recover one. `CallSite` (the only
  context a `Builtin` receives) inherits the same gap. Errors here instead
  carry an ordinal `call_index` -- this call's position among all accepted
  `strategy.*` calls in the run -- which is the closest honest substitute
  available at this layer, not a literal source location.
- `strategy.close()` takes 0 or 1 args: no argument closes the entire
  position (`qty=None` in the recorded intent); one argument closes that
  much of it (same positivity/constant checks as everywhere else).
- Wiring (why `default_builtins()` is untouched). `grammar/parser.py`'s
  `_NAMESPACES` (DSL-3, a file this leaf does not own or modify) is
  currently `{"ta", "math", "series"}` -- `strategy.*` calls cannot appear
  in AIOS Script *source text* yet, so this table is not merged into
  `builtins_ta.default_builtins()` here. `ir/ops.py::Call.ns` itself has no
  such restriction (it is a plain `str`), so `StrategyBuiltins().table` is
  already usable today by any host that builds an `IRProgram` directly
  (this leaf's own tests do exactly that). Extending `_NAMESPACES` so real
  script source can reach it, and merging the table into
  `default_builtins()`/`script_signal_source.py`, is later-leaf work.

Pure module: no I/O, no clock, no recursion (DoD (e)).
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, Literal

from src.core.script.runtime.builtins_math import BuiltinCallError
from src.core.script.runtime.series import Series, Value

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter import Builtin, CallSite

__all__ = ["STRATEGY_KINDS", "StrategyBuiltins", "StrategyIntent", "intents_to_bytes"]

_NS: Final = "strategy"

Side = Literal["long", "short"]
Kind = Literal["entry", "exit", "close", "order"]
STRATEGY_KINDS: Final[tuple[Kind, ...]] = ("entry", "exit", "close", "order")


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    """One accepted `strategy.*` call -- an order *intent*, not a submitted order.

    `qty=None` only happens for a no-argument `strategy.close()` (whole
    position). Every other kind always has a positive, finite `qty`.
    """

    kind: Kind
    side: Side | None
    qty: float | None
    call_index: int


def intents_to_bytes(intents: Sequence[StrategyIntent]) -> bytes:
    """Canonical JSON bytes for an intent sequence (DoD (a) determinism):
    same script + same bar input -> the same bytes, run after run."""
    payload = [asdict(intent) for intent in intents]
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


class StrategyBuiltins:
    """Fresh, stateful `strategy.*` table for one `execute()` run -- mirrors
    `builtins_ta.TaBuiltins`'s per-run call ledger. Construct one instance per
    execution; sharing an instance across runs would mix their intents."""

    def __init__(self) -> None:
        self._intents: list[StrategyIntent] = []
        self.table: dict[tuple[str, str], Builtin] = {
            (_NS, "entry"): self._make("entry", takes_side=True, qty_required=True),
            (_NS, "order"): self._make("order", takes_side=True, qty_required=True),
            (_NS, "exit"): self._make("exit", takes_side=False, qty_required=True),
            (_NS, "close"): self._make("close", takes_side=False, qty_required=False),
        }

    @property
    def intents(self) -> tuple[StrategyIntent, ...]:
        return tuple(self._intents)

    def _make(self, kind: Kind, *, takes_side: bool, qty_required: bool) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call(kind, takes_side, qty_required, args, site)

        return builtin

    def _call(
        self,
        kind: Kind,
        takes_side: bool,
        qty_required: bool,
        args: tuple[Value, ...],
        site: CallSite,
    ) -> Value:
        where = f"strategy.{kind}() [bar_count={site.bar_count}]"
        lo = (1 if takes_side else 0) + (1 if qty_required else 0)
        hi = lo if qty_required else lo + 1
        if not lo <= len(args) <= hi:
            expected = f"{lo}" if lo == hi else f"{lo}~{hi}"
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARITY", f"{where} needs {expected} args, got {len(args)}"
            )
        pos = len(self._intents)
        idx = 0
        side: Side | None = None
        if takes_side:
            side = _resolve_side(where, args[0], pos)
            idx = 1
        qty = _resolve_qty(where, args[idx], pos) if len(args) > idx else None
        self._intents.append(StrategyIntent(kind=kind, side=side, qty=qty, call_index=pos))
        return 0.0 if qty is None else qty


def _resolve_side(where: str, value: Value, call_index: int) -> Side:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: side must be a script-level constant, "
            "not a value that varies per bar",
        )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: side is not numeric: {value!r}"
        )
    if value == 1:
        return "long"
    if value == -1:
        return "short"
    raise BuiltinCallError(
        "SCRIPT_BUILTIN_ARG",
        f"{where} call #{call_index}: side must be 1 (long) or -1 (short), got {value!r}",
    )


def _resolve_qty(where: str, value: Value, call_index: int) -> float:
    if isinstance(value, Series):
        raise BuiltinCallError(
            "SCRIPT_STRATEGY_NONCONSTANT",
            f"{where} call #{call_index}: qty must be a script-level constant, "
            "not a value that varies per bar",
        )
    if value is None:
        raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty is na")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty is not numeric: {value!r}"
        )
    qty = float(value)
    if not math.isfinite(qty) or qty <= 0:
        raise BuiltinCallError(
            "SCRIPT_BUILTIN_ARG", f"{where} call #{call_index}: qty must be positive, got {qty!r}"
        )
    return qty
