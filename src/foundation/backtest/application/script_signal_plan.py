"""BT-10b — order/strategy-intent materialization for `script_signal_source.py`.

Split out of `script_signal_source.py` (task-5686, Guard P6.line_cap) to keep
both files under the 300-line cap. Owns the pure plan-building step: turning
`ExecutionResult.orders` and `StrategyIntent`s into the per-bar `OrderIntent`
plan that `_MaterializedSignalSource.on_bar` looks up. See
`script_signal_source.py` module docstring for the full BT-10b contract
(side/qty_expr/opts rules, no-look-ahead argument, I-05 rationale).

Pure module — no I/O (TID251, backtest/application zone).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from src.core.script.grammar.ast import Expr, Identifier, NumberLiteral
from src.core.script.runtime.builtins_strategy import StrategyIntent
from src.core.script.runtime.interpreter_types import ExecutionResult
from src.core.script.runtime.series import Scalar, ScriptRuntimeError, Series, Value
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import BracketMetadata
from src.foundation.backtest.application.quick_backtest_fill import OrderIntent

__all__ = ["ScriptSignalSourceError"]

_SIDE_BY_NAME: Mapping[str, OrderSide] = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}


class ScriptSignalSourceError(ScriptRuntimeError):
    """Raised when this bridge cannot safely interpret `order()` side/qty_expr/opts
    or when a materialised value violates the `OrderIntent` contract (fail-closed).
    Subclass of `ScriptRuntimeError` so callers can catch "script execution/interpretation
    failure" under one exception hierarchy."""


def _materialize_plan(
    result: ExecutionResult, bar_count: int, strategy_intents: tuple[StrategyIntent, ...]
) -> tuple[Mapping[int, OrderIntent], BracketMetadata | None]:
    """Materialize order declarations and strategy intents into a plan.

    Returns (plan dict, bracket_metadata). Bracket intents are extracted
    separately from regular entry/order intents and not added to the plan.
    """
    plan: dict[int, OrderIntent] = {}
    bracket: BracketMetadata | None = None

    # Materialize order() declarations (DSL-5 production).
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

    # Materialize strategy.* intents (BT-10b, task-5195). Since the DSL has no
    # per-bar conditionals, all strategy intents fire on bar 0 by design.
    for intent in strategy_intents:
        bar_index = 0  # All strategy.* calls fire on bar 0 (no per-bar conditionals).

        # Bracket intents are handled separately and not converted to OrderIntent.
        if intent.kind == "bracket":
            if bracket is not None:
                raise ScriptSignalSourceError(
                    "multiple strategy.bracket() calls on bar 0 — only one bracket allowed"
                )
            bracket = BracketMetadata(
                requested_qty=intent.qty if intent.qty is not None else Decimal("0"),
                profit_price=intent.profit_price,
                loss_price=intent.loss_price,
                trail_pct=intent.trail_pct,
            )
            continue

        if bar_index in plan:
            raise ScriptSignalSourceError(
                f"strategy.* call and order() both fire at bar {bar_index} — priority undefined"
            )
        # Conversion to OrderIntent will raise for exit/close with clear explanation.
        plan[bar_index] = _strategy_intent_to_order_intent(intent)

    return MappingProxyType(plan), bracket


def _strategy_intent_to_order_intent(intent: StrategyIntent) -> OrderIntent:
    """Convert a StrategyIntent to an OrderIntent for backtest consumption.

    Bracket intents are handled specially: this function accepts them but does
    not convert them to OrderIntent (they're not executable as single orders).
    They are instead materialized separately by the caller into bracket metadata.
    """
    if intent.kind == "bracket":
        # Bracket intents are handled specially by _materialize_plan and not
        # converted to OrderIntent. If this is reached, it's a logic error.
        raise ScriptSignalSourceError(
            "bracket intent should be handled specially, not converted to OrderIntent"
        )

    if intent.kind == "entry" or intent.kind == "order":
        # Both entry and order have side ("long" or "short" per StrategyIntent).
        if intent.side is None:
            raise ScriptSignalSourceError(
                f"strategy.{intent.kind}() recorded without side (internal error)"
            )
        # After None check, intent.side is narrowed to "long" | "short"
        side = OrderSide.BUY if intent.side == "long" else OrderSide.SELL
    elif intent.kind in ("exit", "close"):
        # Exit and close don't have a direction — they're exit-only.
        # For now, the backtest doesn't support dedicated exit orders
        # (they'd require position tracking to know which side to close).
        raise ScriptSignalSourceError(
            f"strategy.{intent.kind}() is recorded but not yet executed "
            "by the backtest loop (exits are BT-11 scope: partial-fill bracket exit legs). "
            "Test via dedicated parity tests."
        )
    else:
        raise ScriptSignalSourceError(f"unknown intent kind: {intent.kind!r}")

    return OrderIntent(
        side=side,
        quantity=intent.qty if intent.qty is not None else Decimal("0"),
        order_type=intent.order_type if intent.order_type != "market" else "market",
        trigger_price=intent.trigger_price,
    )


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
