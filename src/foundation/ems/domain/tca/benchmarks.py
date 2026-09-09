"""EM-12 -- domain/tca/benchmarks.py: arrival/VWAP/close price benchmarks (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/tca/{benchmarks,decomposition}.py`), #3 (the TCA benchmark
reference time is fixed to the parent order's arrival_ts and the child
fill window), #4 EM-A5, #9 EM-12.

Three benchmark *prices* only -- no bps composition. `TcaResult`
(`contracts/v1.py`) already owns the bps shape and is left untouched here;
turning a benchmark price into an arrival/vwap/impact/fees/opportunity bps
decomposition is EM-13's `decomposition.py`.

No I/O: every value this module needs (the price observed at
`parent.arrival_ts`, the fills for a slice window, the bars for a period)
is resolved by the caller and passed in as plain data -- this module never
reads a clock, a snapshot store, or the database.

All arithmetic is `Decimal`-only (project-wide convention, standard 105)
-- no value in this module ever round-trips through a binary
floating-point type, since that round-trip would introduce error into a
cost benchmark.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from src.data.models.market_data import Candle
from src.foundation.ems.contracts.v1 import EmsErrorCode


class EmptyFillsError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `fills` is an empty sequence.

    A VWAP benchmark that fell back to `Decimal('0')` for zero fills would
    let a downstream TCA report a false "zero cost" instead of surfacing
    that no execution has happened yet -- fail-closed, no `None`/`0`
    fallback.
    """

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class InvalidFillError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- a fill's `qty` or `price` is not strictly positive."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class EmptyBarsError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `bars` is empty, so there is no close to benchmark against."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


@dataclass(frozen=True)
class Fill:
    """One execution fill contributing to a VWAP benchmark.

    Both fields must be strictly positive -- `compute_vwap` rejects any
    fill that is not (EM-12 DoD (c)).
    """

    price: Decimal
    qty: Decimal


def arrival_price(price_at_arrival_ts: Decimal) -> Decimal:
    """Return the TCA arrival-price benchmark.

    The benchmark reference time is fixed to the parent order's
    `arrival_ts` (spec #3). The caller resolves the market price observed
    at that timestamp (a snapshot lookup that belongs outside this pure
    domain zone) and passes it in as `price_at_arrival_ts`; this function
    reads no clock and no snapshot store, so the same input always
    produces the same output.
    """
    return price_at_arrival_ts


def compute_vwap(fills: Sequence[Fill]) -> Decimal:
    """Return the volume-weighted average price of `fills`.

    Rejects (fail-closed, no `None`/`0` fallback):
    - an empty `fills` sequence (`EmptyFillsError`).
    - any fill with `qty <= 0` or `price <= 0` (`InvalidFillError`).

    `sum(price * qty) / sum(qty)`, computed entirely in `Decimal` -- e.g.
    `[(100, 10), (110, 30)]` yields exactly `Decimal('107.5')`.
    """
    if not fills:
        raise EmptyFillsError(
            "fills is empty -- a VWAP benchmark cannot be computed from zero executions."
        )

    total_notional = Decimal("0")
    total_qty = Decimal("0")
    for fill in fills:
        if fill.qty <= 0:
            raise InvalidFillError(f"fill qty must be > 0, got {fill.qty}.")
        if fill.price <= 0:
            raise InvalidFillError(f"fill price must be > 0, got {fill.price}.")
        total_notional += fill.price * fill.qty
        total_qty += fill.qty

    return total_notional / total_qty


def close_price(bars: Sequence[Candle]) -> Decimal:
    """Return the closing-price benchmark: the `close` of the last bar in `bars`.

    `bars` must be given in chronological order for the last element to be
    meaningful; this function trusts that ordering rather than re-sorting
    (re-sorting would need a wall-clock-independent tie-break rule that no
    caller has specified yet). Rejects an empty `bars` sequence
    (`EmptyBarsError`) rather than returning `Decimal('0')`.
    """
    if not bars:
        raise EmptyBarsError("bars is empty -- a close-price benchmark cannot be computed.")
    return bars[-1].close
