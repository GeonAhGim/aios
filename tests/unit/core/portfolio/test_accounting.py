"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 98, section 9 L21 --
accounting.py tests.

DoD (a)/(b) must all be falsifiable: allowing `available` to go negative,
letting a settle_fill/release step break `cash - reserved - margin_used ==
available`, letting partial fills diverge from one full fill, mutating the
input ledger, or accepting a float amount must each fail some test here.
"""

from __future__ import annotations

import time
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


# --- D2: failure injection (ADR-2026-09-09-C Decision 1) ---------------------


class _CorruptedFill:
    """Duck-typed stand-in for a broken execution-report feed -- `price`,
    `quantity`, `fee`, `slippage_cost` look like a valid fill, but `side`
    raises when read (settle_fill reads it last). settle_fill must not
    swallow this into a silently-wrong ledger; it must propagate fail-closed
    and leave the input ledger untouched (immutability, see file docstring).
    """

    price = Decimal("100")
    quantity = Decimal("1")
    fee = Decimal("1")
    slippage_cost = Decimal("0")

    @property
    def side(self) -> OrderSide:
        raise ConnectionError("execution report feed truncated mid-read")


def test_failure_injection_settle_fill_propagates_corrupted_feed_error_fail_closed():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("300"))
    corrupted = _CorruptedFill()

    with pytest.raises(ConnectionError):
        settle_fill(ledger, corrupted)  # duck-typed fill, not a real SimulatedFill

    # fail-closed: the exception propagated instead of being caught and
    # defaulting to some fabricated cash/reserved delta.
    assert ledger.cash == Decimal("1000")
    assert ledger.reserved == Decimal("300")


# --- D2: numeric performance assertion (pre-trade gate budget, ADR-2026-09-09-C) --


def test_perf_reserve_settle_release_cycle_p99_within_pre_trade_gate_budget():
    """reserve()/settle_fill()/release() sit directly on the pre-trade gate
    path (an order cannot be placed until cash is reserved against it), so
    the "사전거래 게이트 p99 5ms" budget (ADR-2026-09-09-C Decision 1) applies.
    """
    ledger = CashLedger(cash=Decimal("1000000"))
    fill = _fill(
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        slippage_cost=Decimal("0.05"),
    )

    latencies_ms: list[float] = []
    for _ in range(500):
        start = time.perf_counter()
        cycled = reserve(ledger, Decimal("100"))
        cycled = settle_fill(cycled, fill)
        release(cycled, Decimal("0"))
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(len(latencies_ms) * 0.99) - 1]
    assert p99 < 5.0, f"p99 {p99:.3f}ms exceeds pre-trade gate budget of 5ms (ADR-2026-09-09-C)"


# --- D2: gate-red reproduction -------------------------------------------------


def _broken_reserve_without_guard(ledger: CashLedger, notional: Decimal) -> CashLedger:
    """Gate-red reproduction: a regression that drops the `new_available < 0`
    guard from the real `reserve()` (accounting.py) would silently let a
    reservation push `available` negative instead of refusing it -- contrast
    with the real implementation's fail-closed `PortfolioInsufficientCashError`
    for the identical input below.
    """
    new_reserved = ledger.reserved + notional
    return CashLedger(cash=ledger.cash, reserved=new_reserved, margin_used=ledger.margin_used)


def test_gate_red_reserve_without_guard_would_silently_go_negative():
    ledger = CashLedger(cash=Decimal("1000"), reserved=Decimal("900"))

    # red: guard-less regression silently corrupts the ledger
    red_result = _broken_reserve_without_guard(ledger, Decimal("200"))
    assert red_result.available == Decimal("-100")

    # green: the real reserve() refuses the same input instead
    with pytest.raises(PortfolioInsufficientCashError):
        reserve(ledger, Decimal("200"))
