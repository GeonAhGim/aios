"""EM-13 -- domain/tca/decomposition.py: cost decomposition (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/tca/{benchmarks,decomposition}.py`), #4 EM-A5, #9 EM-13.

Turns the three EM-12 benchmark prices (arrival, execution VWAP, close)
into a five-way cost breakdown -- spread, market impact, delay, fees,
residual -- that reconciles *exactly* to an externally supplied
`total_cost` (DoD (b)). `total_cost` is not re-derived from the four
attributed components: it is the authoritative, independently sourced
cost figure (e.g. actual cash debited, already rounded to a currency's
minor unit by the caller), while `spread_cost`, `market_impact`,
`delay_cost` and `fees` are computed/observed at full precision. Any gap
between the two is arithmetic, not noise -- `residual` is defined as
that exact gap (`total_cost - (spread + impact + delay + fees)`), so the
five components sum to `total_cost` with zero rounding error by
construction, never by chance.

`market_impact` and `delay_cost` are derived here from the EM-12
benchmark prices -- this module takes `arrival_price`/`vwap_price`/
`close_price` as plain `Decimal` inputs (already produced by
`benchmarks.arrival_price`/`compute_vwap`/`close_price`) and never
recomputes them from raw fills or bars (DoD (a)). `spread_cost` and
`fees` cannot be derived from those three prices alone -- crossing the
bid-ask spread needs quote data, and fees need the venue's fee schedule
-- so both are resolved by the caller and passed in as plain data, the
same "no I/O in this module" convention `benchmarks.py` uses.

No I/O: no clock, no snapshot store, no database. All arithmetic is
`Decimal`-only (project-wide convention, standard 105).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import EmsErrorCode
from src.foundation.ems.domain.tca.benchmarks import Fill

_SIDE_SIGN: dict[OrderSide, Decimal] = {
    OrderSide.BUY: Decimal("1"),
    OrderSide.SELL: Decimal("-1"),
}


class EmptyFillsError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `fills` is an empty sequence.

    A decomposition that fell back to `Decimal('0')` executed quantity
    for zero fills would silently zero out `market_impact`/`delay_cost`
    and let `residual` absorb the entire cost -- fail-closed, no
    `None`/`0` fallback (EM-13 DoD (c)).
    """

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class InvalidFillError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- a fill's `qty` or `price` is not strictly positive."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


@dataclass(frozen=True)
class CostDecomposition:
    """The five-way split of `total_cost`. Signed in the order's cost
    currency: positive means the component made execution more
    expensive, negative means it made execution cheaper.

    `spread_cost + market_impact + delay_cost + fees + residual ==
    total_cost` holds exactly for every instance -- `decompose_cost` is
    the only constructor that should be used, since it is what computes
    `residual` as the balancing figure.
    """

    spread_cost: Decimal
    market_impact: Decimal
    delay_cost: Decimal
    fees: Decimal
    residual: Decimal
    total_cost: Decimal


def _total_filled_qty(fills: Sequence[Fill]) -> Decimal:
    if not fills:
        raise EmptyFillsError(
            "fills is empty -- a cost decomposition cannot be computed from zero executions."
        )

    total_qty = Decimal("0")
    for fill in fills:
        if fill.qty <= 0:
            raise InvalidFillError(f"fill qty must be > 0, got {fill.qty}.")
        if fill.price <= 0:
            raise InvalidFillError(f"fill price must be > 0, got {fill.price}.")
        total_qty += fill.qty
    return total_qty


def decompose_cost(
    fills: Sequence[Fill],
    side: OrderSide,
    arrival_price: Decimal,
    vwap_price: Decimal,
    close_price: Decimal,
    spread_cost: Decimal,
    fees: Decimal,
    total_cost: Decimal,
) -> CostDecomposition:
    """Decompose `total_cost` into spread/impact/delay/fees/residual.

    `arrival_price`, `vwap_price` and `close_price` are the EM-12
    benchmark outputs for this parent order's fill window (not
    recomputed here -- see module docstring). `spread_cost` and `fees`
    are resolved by the caller from quote and fee-schedule data this
    module does not have access to. `total_cost` is the independently
    sourced, authoritative cost figure this decomposition must
    reconcile to.

    `market_impact = (vwap_price - arrival_price) * total_qty * sign`
    is the cost of executing at the realized VWAP instead of the
    arrival benchmark. `delay_cost = (close_price - vwap_price) *
    total_qty * sign` is the cost of price drift across the fill
    window, measured against the terminal close benchmark. `sign` is
    `+1` for a BUY (a higher price is worse) and `-1` for a SELL (a
    lower price is worse).

    Rejects (fail-closed, no `None`/`0` fallback):
    - an empty `fills` sequence (`EmptyFillsError`).
    - any fill with `qty <= 0` or `price <= 0` (`InvalidFillError`).
    """
    total_qty = _total_filled_qty(fills)
    sign = _SIDE_SIGN[side]

    market_impact = (vwap_price - arrival_price) * total_qty * sign
    delay_cost = (close_price - vwap_price) * total_qty * sign
    residual = total_cost - (spread_cost + market_impact + delay_cost + fees)

    return CostDecomposition(
        spread_cost=spread_cost,
        market_impact=market_impact,
        delay_cost=delay_cost,
        fees=fees,
        residual=residual,
        total_cost=total_cost,
    )
