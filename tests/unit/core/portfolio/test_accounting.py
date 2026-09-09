"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 98, section 9 L21 --
accounting.py tests.

DoD (a)/(b) must all be falsifiable: allowing `available` to go negative,
letting a settle_fill/release step break `cash - reserved - margin_used ==
available`, letting partial fills diverge from one full fill, mutating the
input ledger, or accepting a float amount must each fail some test here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.portfolio.accounting import (
    CashLedger,
    PortfolioInsufficientCashError,
    release,
    reserve,
    settle_fill,
)
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import SimulatedFill

_TS = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _fill(
    *,
    side: OrderSide,
    price: Decimal,
    quantity: Decimal,
    fee: Decimal,
    slippage_cost: Decimal,
) -> SimulatedFill:
    return SimulatedFill(
        bar_index=0,
        timestamp=_TS,
        symbol="BTC/USDT",
        side=side,
        price=price,
        quantity=quantity,
        fee=fee,
        slippage_cost=slippage_cost,
    )


def _invariant_holds(ledger: CashLedger) -> bool:
    return ledger.cash - ledger.reserved - ledger.margin_used == ledger.available


# --- CashLedger shape (margin_used exists even though Phase 1 keeps it 0) ----


def test_cash_ledger_has_cash_reserved_margin_used_and_derives_available():
    ledger = CashLedger(cash=Decimal("1000"))
    assert ledger.reserved == Decimal("0")
    assert ledger.margin_used == Decimal("0")
    assert ledger.available == Decimal("1000")


# --- (a) reserve() rejects a reservation that would make available negative -


def test_reserve_rejects_when_available_would_go_negative():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("900"))
    with pytest.raises(PortfolioInsufficientCashError):
        reserve(ledger, Decimal("101"))


def test_reserve_allows_the_exact_boundary_of_zero_available():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("900"))
    result = reserve(ledger, Decimal("100"))
    assert result.available == Decimal("0")


def test_reserve_does_not_mutate_the_input_ledger():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("900"))
    reserve(ledger, Decimal("50"))
    assert ledger.reserved == Decimal("900")
    assert ledger.available == Decimal("100")


# --- (b) invariant holds across reserve/settle_fill/release ------------------


def test_invariant_holds_after_reserve_settle_release():
    ledger = CashLedger(cash=Decimal("1000"))
    ledger = reserve(ledger, Decimal("300"))
    assert _invariant_holds(ledger)

    fill = _fill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("3"),
        fee=Decimal("3"),
        slippage_cost=Decimal("1.5"),
    )
    ledger = settle_fill(ledger, fill)
    assert _invariant_holds(ledger)

    ledger = release(ledger, Decimal("0"))
    assert _invariant_holds(ledger)


def test_settle_fill_buy_spends_cash_and_releases_the_matching_reservation():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("300"))
    fill = _fill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("3"),
        fee=Decimal("3"),
        slippage_cost=Decimal("1.5"),
    )
    result = settle_fill(ledger, fill)
    assert result.cash == Decimal("1000") - Decimal("304.5")
    assert result.reserved == Decimal("0")


def test_settle_fill_sell_credits_cash_and_leaves_reserved_untouched():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("50"))
    fill = _fill(
        side=OrderSide.SELL,
        price=Decimal("100"),
        quantity=Decimal("3"),
        fee=Decimal("3"),
        slippage_cost=Decimal("1.5"),
    )
    result = settle_fill(ledger, fill)
    assert result.cash == Decimal("1000") + Decimal("295.5")
    assert result.reserved == Decimal("50")


def test_settle_fill_does_not_mutate_the_input_ledger():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("300"))
    fill = _fill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("1"),
        slippage_cost=Decimal("0"),
    )
    settle_fill(ledger, fill)
    assert ledger.cash == Decimal("1000")
    assert ledger.reserved == Decimal("300")


# --- three partial fills == one full fill, exact Decimal equality -----------


def test_three_partial_fills_equal_one_full_fill_exactly():
    starting = CashLedger(cash=Decimal("10000"), reserved=Decimal("300"))

    full_fill = _fill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("3"),
        fee=Decimal("3"),
        slippage_cost=Decimal("1.5"),
    )
    via_full = settle_fill(starting, full_fill)

    via_partials = starting
    for _ in range(3):
        partial = _fill(
            side=OrderSide.BUY,
            price=Decimal("100"),
            quantity=Decimal("1"),
            fee=Decimal("1"),
            slippage_cost=Decimal("0.5"),
        )
        via_partials = settle_fill(via_partials, partial)

    assert via_partials.cash == via_full.cash
    assert via_partials.reserved == via_full.reserved
    assert via_partials.margin_used == via_full.margin_used


# --- Decimal-only enforcement (105 standard) ---------------------------------


def test_cash_ledger_rejects_float_cash():
    with pytest.raises(ValidationError):
        CashLedger(cash=1000.0)


def test_cash_ledger_rejects_float_reserved():
    with pytest.raises(ValidationError):
        CashLedger(cash=Decimal("1000"), reserved=50.0)
