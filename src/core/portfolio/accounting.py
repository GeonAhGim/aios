"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 98, section 9 L21 --
accounting.py.

Pure cash/margin ledger state machine. `available` is a derived value, never
a stored field, so the invariant `cash - reserved - margin_used == available`
holds by construction after every transition -- there is no code path that
could desynchronize it. Phase 1 is spot-only so `margin_used` is always 0,
but the field exists because the spec (row 98) requires it for a future
margin phase to populate without changing this module's public contract.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import SimulatedFill


def _reject_float(value: Any) -> Any:
    """Reject a python float before pydantic coerces it into a Decimal field.

    pydantic's lax mode silently converts float -> Decimal, which would let
    binary floating-point error leak into a cash ledger (105 standard).
    """
    if isinstance(value, float):
        raise ValueError("float is not accepted here -- pass Decimal")
    return value


class PortfolioInsufficientCashError(ValueError):
    """PORTFOLIO_INSUFFICIENT_CASH -- reserving this notional would push
    `available` below zero. The caller must not clamp to zero or borrow;
    the reservation is refused outright and the order must not be placed.
    """


class CashLedger(BaseModel):
    """Cash/reserved/margin ledger for one execution's account.

    Frozen (immutable): every transition below returns a new `CashLedger`
    instead of mutating this one, so a reference held by a caller before a
    transition still reflects the pre-transition state.
    """

    model_config = ConfigDict(frozen=True)

    cash: Decimal
    reserved: Decimal = Decimal("0")
    margin_used: Decimal = Decimal("0")

    @field_validator("cash", "reserved", "margin_used", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @property
    def available(self) -> Decimal:
        return self.cash - self.reserved - self.margin_used


def reserve(ledger: CashLedger, notional: Decimal) -> CashLedger:
    """Reserve `notional` of cash against a not-yet-filled order.

    Rejects with `PortfolioInsufficientCashError` when the reservation would
    take `available` negative -- the boundary itself (`available == 0`) is
    allowed.
    """
    new_reserved = ledger.reserved + notional
    new_available = ledger.cash - new_reserved - ledger.margin_used
    if new_available < 0:
        raise PortfolioInsufficientCashError(
            f"reserving {notional} would take available to {new_available} "
            "-- refusing instead of allowing a negative cash balance"
        )
    return CashLedger(cash=ledger.cash, reserved=new_reserved, margin_used=ledger.margin_used)


def release(ledger: CashLedger, notional: Decimal) -> CashLedger:
    """Release a previously reserved notional (order canceled or rejected)."""
    return CashLedger(
        cash=ledger.cash,
        reserved=ledger.reserved - notional,
        margin_used=ledger.margin_used,
    )


def settle_fill(ledger: CashLedger, fill: SimulatedFill) -> CashLedger:
    """Apply one fill to the ledger.

    A BUY fill spends `price * quantity + fee + slippage_cost` of cash and
    releases the matching notional from `reserved` (the amount `reserve()`
    set aside when the order was placed -- this spot-only ledger never
    reserves cash for a SELL, since a SELL delivers an existing position,
    not cash). A SELL fill credits `price * quantity - fee - slippage_cost`
    to cash and leaves `reserved` untouched.

    Applying three partial fills that sum to one fill's quantity/fee/
    slippage yields the exact same ledger as applying that one fill --
    every operation here is Decimal addition/subtraction, never division,
    so no rounding step can break that equivalence.
    """
    notional = fill.price * fill.quantity
    costs = fill.fee + fill.slippage_cost
    if fill.side == OrderSide.BUY:
        cash = ledger.cash - notional - costs
        reserved = ledger.reserved - notional
    else:
        cash = ledger.cash + notional - costs
        reserved = ledger.reserved
    return CashLedger(cash=cash, reserved=reserved, margin_used=ledger.margin_used)
