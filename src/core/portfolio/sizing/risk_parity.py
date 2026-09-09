"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 93 — risk-parity sizing.

Inverse-volatility weighting across runs, `w_i ∝ 1/σ_i`, summing to 1.
`PortfolioAggregate` (L18) only preserves the per-strategy exposure share
(`per_strategy_pct`) and discards each individual run's volatility, so this
leaf cannot look up the actual σ_j of each other run — within that
constraint it uses a **2-block simplification (new position vs. total
existing exposure)**: total existing exposure
(`exposures.total_exposure_pct`) is treated as a single counterparty, and
the new position's share is split against it in proportion to `1/σ_i`.

If `w_i = (1/σ_i) / (1/σ_i + S)` with `S = existing_fraction`, then
algebraically `w_i + S/(1/σ_i + S) = 1` always holds (§8 test table
"risk-parity weights sum to 1"). `existing_block_weight_fraction()` returns
that second term, so tests can directly verify the two sum to 1.

A true N-asset risk parity that preserves each of multiple strategies'
individual volatility cannot be computed with this signature (`size(inp)`,
a single run's input) until L18 adds a per-strategy vol field — unverified.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.portfolio.config import SizingMethod
from src.core.portfolio.sizing import (
    HUNDRED,
    SizingResult,
    hash_inputs,
    require,
    require_positive,
)
from src.core.portfolio.state_input import PortfolioStateInput

_ONE = Decimal("1")


def _denominator(inp: PortfolioStateInput) -> tuple[Decimal, Decimal, Decimal]:
    """`(inv_vol, existing_fraction, denom)` — `size()` and
    `existing_block_weight_fraction()` must share these same three values for
    the two results to sum to exactly 1 algebraically."""
    exposures = require(inp.exposures, "exposures")
    realized_vol_pct = require(inp.realized_vol_pct, "realized_vol_pct")
    require_positive(realized_vol_pct, "realized_vol_pct")

    existing_fraction = exposures.total_exposure_pct / HUNDRED
    inv_vol = _ONE / realized_vol_pct
    return inv_vol, existing_fraction, inv_vol + existing_fraction


def existing_block_weight_fraction(inp: PortfolioStateInput) -> Decimal:
    """The share taken by the existing-exposure block — always sums to 1 when
    added to `size()`'s returned `weight_pct/100`."""
    _inv_vol, existing_fraction, denom = _denominator(inp)
    return existing_fraction / denom


def size(inp: PortfolioStateInput) -> SizingResult:
    inv_vol, _existing_fraction, denom = _denominator(inp)
    require_positive(inp.current_price, "current_price")
    require_positive(inp.total_equity, "total_equity")

    weight_fraction = inv_vol / denom
    weight_pct = weight_fraction * HUNDRED
    notional = inp.total_equity * weight_fraction
    quantity = notional / inp.current_price

    exposures = require(inp.exposures, "exposures")
    realized_vol_pct = require(inp.realized_vol_pct, "realized_vol_pct")

    return SizingResult(
        quantity=quantity,
        weight_pct=weight_pct,
        method=SizingMethod.RISK_PARITY,
        inputs_hash=hash_inputs(
            SizingMethod.RISK_PARITY,
            realized_vol_pct=realized_vol_pct,
            total_exposure_pct=exposures.total_exposure_pct,
            current_price=inp.current_price,
        ),
    )
