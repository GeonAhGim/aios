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
  NUMERIC_TYPES, and there is no bool or string type anywhere in this
  grammar, so `side` cannot be a `buy`/`sell` identifier the way the
  dedicated `order(...) when expr` production allows (`grammar/ast.py::
  OrderDecl.side` is an unchecked bare `Expr`). This leaf encodes direction
  as a plain numeric constant instead: `1` = long, `-1` = short.
- Constant-only side/qty. A statically "float"-typed value can still be a
  runtime `Series` (e.g. `close[1]`) because DSL-8 executes the whole bar
  range at once. `src/foundation/backtest/application/script_signal_source.py`
  (BT-10b, this leaf's eventual consumer) only accepts a constant side/qty
  per order -- this module enforces the identical rule at call time via
  `isinstance(value, Series)`, independent of what the compiler inferred.
- Position info. The AST/IR carry no (line, col) (DSL-4/DSL-5, checker.py/
  lookahead.py) -- `CallSite` inherits the same gap. Errors here instead
  carry an ordinal `call_index`, the closest honest substitute available.
- `strategy.close()` takes 0 or 1 args: no argument closes the entire
  position (`qty=None` in the recorded intent); one argument closes that
  much of it (same positivity/constant checks as everywhere else).
- Limit/stop price encoding (task-2623, ADR-2026-09-09-B). `entry`/`order`
  accept an optional trailing pair `(trigger_price, type_code)` -- both or
  neither (arity 2 or 4). `exit` gets the same pair after its one required
  `qty` (arity 1 or 3). `type_code` reuses the side encoding's `1`/`-1`
  convention (`1` = limit, `-1` = stop). Recorded as pure data -- this leaf
  has no market data to check a limit price against.
- `strategy.bracket(qty, profit_price, loss_price, trail_pct)` -- a new
  `bracket` kind (always 4 args, no side: exits whatever position is open).
  Each leg may be `na` (absent), but not all three -- a zero-leg bracket is
  rejected, not a silent no-op. The legs form an OCA group by construction:
  BT-6 `order_types.resolve_oca` and the isomorphic live
  `src/services/oms/domain/order_types/oca.py` turn "which legs present"
  into "which leg fired, which cancelled" (parity verified there, not
  here -- this leaf only records the three prices).
- Argument resolvers live in the sibling `builtins_strategy_args.py`, not
  this file -- task-2623 pushed this file over the zone's 300-line cap
  (same reason BT-10's `quick_backtest_fill.py` split off `quick_backtest.py`).
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
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal

from src.core.script.runtime.builtins_math import BuiltinCallError
from src.core.script.runtime.builtins_strategy_args import (
    OrderTypeName,
    Side,
    resolve_optional_price,
    resolve_order_type,
    resolve_price,
    resolve_qty,
    resolve_side,
    resolve_trail_pct,
)

if TYPE_CHECKING:
    from src.core.script.runtime.interpreter import Builtin, CallSite
    from src.core.script.runtime.series import Value

__all__ = [
    "STRATEGY_KINDS",
    "OrderTypeName",
    "StrategyBuiltins",
    "StrategyIntent",
    "intents_to_bytes",
]

_NS: Final = "strategy"

Kind = Literal["entry", "exit", "close", "order", "bracket"]
STRATEGY_KINDS: Final[tuple[Kind, ...]] = ("entry", "exit", "close", "order", "bracket")


@dataclass(frozen=True, slots=True)
class StrategyIntent:
    """One accepted `strategy.*` call -- an order *intent*, not a submitted order.

    `qty=None` only happens for a no-argument `strategy.close()` (whole
    position). Every other kind always has a positive, finite `qty`.

    `order_type`/`trigger_price` apply to `entry`/`order`/`exit` (limit/stop
    price on the order itself). `profit_price`/`loss_price`/`trail_pct`
    apply only to `bracket` (the OCA exit legs) -- each present field is one
    active leg, `None` means that leg is absent.
    """

    kind: Kind
    side: Side | None
    qty: Decimal | None
    call_index: int
    order_type: OrderTypeName = "market"
    trigger_price: Decimal | None = None
    profit_price: Decimal | None = None
    loss_price: Decimal | None = None
    trail_pct: Decimal | None = None


def _json_default(value: object) -> str:
    """`qty` is `Decimal` (order-quantity precision, not DSL float arithmetic) --
    plain `json.dumps` has no native Decimal support. Mirrors `allow_nan=False`
    for the one type it doesn't itself inspect, so a corrupted non-finite
    Decimal fails closed here too instead of round-tripping as `"NaN"`."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite qty in intent payload: {value!r}")
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def intents_to_bytes(intents: Sequence[StrategyIntent]) -> bytes:
    """Canonical JSON bytes for an intent sequence (DoD (a) determinism):
    same script + same bar input -> the same bytes, run after run."""
    payload = [asdict(intent) for intent in intents]
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


class StrategyBuiltins:
    """Fresh, stateful `strategy.*` table for one `execute()` run -- mirrors
    `builtins_ta.TaBuiltins`'s per-run call ledger. Construct one instance per
    execution; sharing an instance across runs would mix their intents."""

    def __init__(self) -> None:
        self._intents: list[StrategyIntent] = []
        self.table: dict[tuple[str, str], Builtin] = {
            (_NS, "entry"): self._make_order("entry"),
            (_NS, "order"): self._make_order("order"),
            (_NS, "exit"): self._make_exit(),
            (_NS, "close"): self._make_close(),
            (_NS, "bracket"): self._make_bracket(),
        }

    @property
    def intents(self) -> tuple[StrategyIntent, ...]:
        return tuple(self._intents)

    def _make_order(self, kind: Kind) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call_order(kind, args, site)

        return builtin

    def _call_order(self, kind: Kind, args: tuple[Value, ...], site: CallSite) -> Value:
        # `entry`/`order`: `(side, qty)` market, or `(side, qty, trigger_price,
        # type_code)` limit/stop -- the pair is all-or-nothing (3 is rejected).
        where = f"strategy.{kind}() [bar_count={site.bar_count}]"
        if len(args) not in (2, 4):
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARITY", f"{where} needs 2 or 4 args, got {len(args)}"
            )
        pos = len(self._intents)
        side = resolve_side(where, args[0], pos)
        qty = resolve_qty(where, args[1], pos)
        order_type: OrderTypeName = "market"
        trigger_price: Decimal | None = None
        if len(args) == 4:
            trigger_price = resolve_price(where, args[2], pos, "trigger_price")
            order_type = resolve_order_type(where, args[3], pos)
        self._intents.append(
            StrategyIntent(
                kind=kind,
                side=side,
                qty=Decimal(str(qty)),
                call_index=pos,
                order_type=order_type,
                trigger_price=trigger_price,
            )
        )
        return qty

    def _make_exit(self) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call_exit(args, site)

        return builtin

    def _call_exit(self, args: tuple[Value, ...], site: CallSite) -> Value:
        # `(qty)` market, or `(qty, trigger_price, type_code)` limit/stop.
        where = f"strategy.exit() [bar_count={site.bar_count}]"
        if len(args) not in (1, 3):
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARITY", f"{where} needs 1 or 3 args, got {len(args)}"
            )
        pos = len(self._intents)
        qty = resolve_qty(where, args[0], pos)
        order_type: OrderTypeName = "market"
        trigger_price: Decimal | None = None
        if len(args) == 3:
            trigger_price = resolve_price(where, args[1], pos, "trigger_price")
            order_type = resolve_order_type(where, args[2], pos)
        self._intents.append(
            StrategyIntent(
                kind="exit",
                side=None,
                qty=Decimal(str(qty)),
                call_index=pos,
                order_type=order_type,
                trigger_price=trigger_price,
            )
        )
        return qty

    def _make_close(self) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call_close(args, site)

        return builtin

    def _call_close(self, args: tuple[Value, ...], site: CallSite) -> Value:
        where = f"strategy.close() [bar_count={site.bar_count}]"
        if not 0 <= len(args) <= 1:
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARITY", f"{where} needs 0~1 args, got {len(args)}"
            )
        pos = len(self._intents)
        qty = resolve_qty(where, args[0], pos) if args else None
        # `qty` stays a DSL `float` for the return value (every `strategy.*`
        # call's result feeds back into float/Series arithmetic elsewhere in
        # the interpreter) -- only the recorded intent switches to `Decimal`,
        # since that's the value a later leaf turns into an actual order qty.
        intent_qty = None if qty is None else Decimal(str(qty))
        self._intents.append(
            StrategyIntent(kind="close", side=None, qty=intent_qty, call_index=pos)
        )
        return 0.0 if qty is None else qty

    def _make_bracket(self) -> Builtin:
        def builtin(args: tuple[Value, ...], site: CallSite) -> Value:
            return self._call_bracket(args, site)

        return builtin

    def _call_bracket(self, args: tuple[Value, ...], site: CallSite) -> Value:
        where = f"strategy.bracket() [bar_count={site.bar_count}]"
        if len(args) != 4:
            raise BuiltinCallError("SCRIPT_BUILTIN_ARITY", f"{where} needs 4 args, got {len(args)}")
        pos = len(self._intents)
        qty = resolve_qty(where, args[0], pos)
        profit_price = resolve_optional_price(where, args[1], pos, "profit_price")
        loss_price = resolve_optional_price(where, args[2], pos, "loss_price")
        trail_pct = resolve_trail_pct(where, args[3], pos)
        if profit_price is None and loss_price is None and trail_pct is None:
            raise BuiltinCallError(
                "SCRIPT_BUILTIN_ARG",
                f"{where} call #{pos}: at least one of profit_price/loss_price/trail_pct "
                "must be set (not all na)",
            )
        self._intents.append(
            StrategyIntent(
                kind="bracket",
                side=None,
                qty=Decimal(str(qty)),
                call_index=pos,
                profit_price=profit_price,
                loss_price=loss_price,
                trail_pct=trail_pct,
            )
        )
        return qty
