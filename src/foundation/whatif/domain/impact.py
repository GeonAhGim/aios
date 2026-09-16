"""UX-9 what-if impact: delta between before/after portfolio states.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#UX-9
ADR-2026-09-09-C: depth=D2 — pure domain function, no I/O.

`delta_impact()` takes a before-state snapshot and a single proposed
trade, returns a `TradeImpact` record with per-metric deltas.
All monetary amounts are `Decimal`; timestamps are tz-aware UTC.

The function does NOT execute or validate the trade — it only computes
the arithmetic delta so the UI can render "what if this order fills".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from src.core.portfolio.state_input import PortfolioAggregate

# ---------------------------------------------------------------------------
# Input / output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedTrade:
    """A single proposed order whose impact is being simulated."""

    symbol: str
    notional: Decimal
    side: str  # "BUY" or "SELL" — used for cash delta sign convention
    strategy_id: str | None = None  # None = portfolio-level (no strategy delta)


@dataclass(frozen=True)
class TradeImpact:
    """Delta between before-state and after-state for one proposed trade."""

    as_of: datetime
    symbol: str
    notional_delta: Decimal  # + for BUY, - for SELL
    cash_delta: Decimal  # - for BUY (cash out), + for SELL (cash in)
    total_equity_delta: Decimal  # = notional_delta + cash_delta (net zero for pure swap)
    position_count_delta: int  # +1 on BUY, -1 on SELL (if position crosses zero)
    gross_notional_delta: Decimal  # ABS increases on BUY, ABS decreases on SELL
    # Per-symbol exposure delta: new_pct - old_pct (may be negative)
    per_symbol_pct_delta: dict[str, Decimal]
    # Per-strategy exposure delta (only when strategy_id is given)
    per_strategy_pct_delta: dict[str, Decimal]
    # Cash pct delta
    cash_pct_delta: Decimal
    # Total exposure pct delta
    total_exposure_pct_delta: Decimal


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def delta_impact(
    *,
    before: PortfolioAggregate,
    trade: ProposedTrade,
) -> TradeImpact:
    """Compute delta between *before* state and *after* state for one trade.

    Parameters
    ----------
    before:
        Current portfolio aggregate (from `PortfolioAggregate`).
    trade:
        The proposed trade whose impact is being simulated.

    Returns
    -------
    TradeImpact
        Per-metric delta (after - before). All values are pure arithmetic —
        no DB access, no exchange call, no validation gate.

    Raises
    ------
    ValueError
        If `trade.notional` is zero or negative (cannot compute meaningful delta).
    """
    if trade.notional <= Decimal("0"):
        raise ValueError(f"trade.notional must be positive, got {trade.notional}")

    # --- cash delta: BUY drains cash, SELL returns cash ---
    if trade.side == "BUY":
        cash_delta = -trade.notional
    elif trade.side == "SELL":
        cash_delta = trade.notional
    else:
        raise ValueError(f"unknown side: {trade.side!r}")

    # --- total equity delta: exposure changes by notional, cash changes by
    # cash_delta. Net effect is zero for a pure cash<->exposure swap. ---
    total_equity_delta = trade.notional + cash_delta  # = 0 for valid sides

    # --- position count delta ---
    # BUY adds one open position (symbol didn't exist or was flat).
    # SELL removes one (symbol goes flat). Simplification: always ±1.
    position_count_delta = 1 if trade.side == "BUY" else -1

    # --- gross notional delta ---
    # BUY: adds new notional to gross (ABS increases).
    # SELL: reduces gross by the notional being closed.
    if trade.side == "BUY":
        gross_notional_delta = trade.notional
    else:
        # SELL reduces gross exposure by the notional amount being closed.
        gross_notional_delta = -trade.notional

    # --- per-symbol pct delta ---
    # old_pct = before.per_symbol_pct.get(symbol, 0)
    # new_equity = before.total_equity + total_equity_delta
    # new_notional = before_per_symbol.get(symbol, 0) + trade.notional (BUY)
    #                or ... - trade.notional (SELL)
    # new_pct = new_notional / new_equity * 100
    # delta = new_pct - old_pct
    new_equity = before.total_equity + total_equity_delta
    if new_equity <= 0:
        raise ValueError(f"after-state total_equity must be positive, got {new_equity}")

    per_symbol_pct_delta: dict[str, Decimal] = {}

    # Compute before-state notional for the trade symbol
    before_notional = Decimal("0")
    if before.total_equity > 0 and trade.symbol in before.per_symbol_pct:
        before_notional = before.per_symbol_pct[trade.symbol] / Decimal("100") * before.total_equity

    if trade.side == "BUY":
        after_notional = before_notional + trade.notional
    else:
        after_notional = max(before_notional - trade.notional, Decimal("0"))

    old_pct = before.per_symbol_pct.get(trade.symbol, Decimal("0"))
    new_pct = after_notional / new_equity * Decimal("100")
    per_symbol_pct_delta[trade.symbol] = new_pct - old_pct

    # For all other symbols, their pct changes because denominator (equity)
    # changed — compute the shift.
    for sym, pct in before.per_symbol_pct.items():
        if sym == trade.symbol:
            continue
        # Old notional for this symbol
        sym_notional = pct / Decimal("100") * before.total_equity
        # New pct with new equity denominator
        new_sym_pct = sym_notional / new_equity * Decimal("100")
        per_symbol_pct_delta[sym] = new_sym_pct - pct

    # --- per-strategy pct delta ---
    per_strategy_pct_delta: dict[str, Decimal] = {}

    if trade.strategy_id is not None:
        old_strat_pct = before.per_strategy_pct.get(trade.strategy_id, Decimal("0"))
        # Strategy notional: derive from pct
        strat_notional = (
            old_strat_pct / Decimal("100") * before.total_equity
            if before.total_equity > 0
            else Decimal("0")
        )
        if trade.side == "BUY":
            after_strat_notional = strat_notional + trade.notional
        else:
            after_strat_notional = max(strat_notional - trade.notional, Decimal("0"))
        new_strat_pct = after_strat_notional / new_equity * Decimal("100")
        per_strategy_pct_delta[trade.strategy_id] = new_strat_pct - old_strat_pct

        # Other strategies shift due to equity change
        for sid, pct in before.per_strategy_pct.items():
            if sid == trade.strategy_id:
                continue
            s_notional = pct / Decimal("100") * before.total_equity
            new_s_pct = s_notional / new_equity * Decimal("100")
            per_strategy_pct_delta[sid] = new_s_pct - pct

    # --- cash pct delta ---
    old_cash_pct = before.cash_pct
    # Simplified: cash_delta / new_equity * 100
    new_cash_pct = (
        (before.total_equity * old_cash_pct / Decimal("100") + cash_delta)
        / new_equity
        * Decimal("100")
    )
    cash_pct_delta = new_cash_pct - old_cash_pct

    # --- total exposure pct delta ---
    old_total_exposure_pct = before.total_exposure_pct
    new_total_notional = (
        before.total_equity * old_total_exposure_pct / Decimal("100") + gross_notional_delta
    )
    new_total_exposure_pct = new_total_notional / new_equity * Decimal("100")
    total_exposure_pct_delta = new_total_exposure_pct - old_total_exposure_pct

    return TradeImpact(
        as_of=datetime.now(timezone.utc),
        symbol=trade.symbol,
        notional_delta=trade.notional,
        cash_delta=cash_delta,
        total_equity_delta=total_equity_delta,
        position_count_delta=position_count_delta,
        gross_notional_delta=gross_notional_delta,
        per_symbol_pct_delta=per_symbol_pct_delta,
        per_strategy_pct_delta=per_strategy_pct_delta,
        cash_pct_delta=cash_pct_delta,
        total_exposure_pct_delta=total_exposure_pct_delta,
    )
