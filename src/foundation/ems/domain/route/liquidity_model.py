"""EM-4 -- venue liquidity depth scoring (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §2 module table
(`domain/route/liquidity_model.py`), §9 EM-4.

Answers "how much of this order can the venue's book absorb right now,
within an acceptable slippage budget" as a normalized `[0, 1]` score --
`1` means the book can fully absorb `desired_qty` without moving the
price beyond `max_slippage_bps` off the top of book, `0` means the venue
currently has no usable depth for this side at all. It does not fetch a
book snapshot (I/O) and does not combine this score with fee cost or
recent fill rate into one venue ranking -- that synthesis is EM-5's
`venue_scoring.py`, per the 2026-09-08 leaf decision. The book snapshot
is a caller-supplied argument, not a new market-data client here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class BookLevel:
    """One price level of an order-book side."""

    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("BookLevel.price must be > 0.")
        if self.size <= 0:
            raise ValueError("BookLevel.size must be > 0.")


def depth_absorption_score(
    levels: Sequence[BookLevel],
    side: Side,
    desired_qty: Decimal,
    max_slippage_bps: Decimal,
) -> Decimal:
    """Fraction of `desired_qty` the book can fill within `max_slippage_bps`.

    `levels` must be the side of the book an order of this `side` walks
    against -- asks (best/lowest price first, ascending) for `BUY`, bids
    (best/highest price first, descending) for `SELL` -- matching the
    same best-first convention as market-data's `BookL2` contract. A book
    out of that order is rejected rather than silently re-sorted, since a
    wrong order upstream usually signals a feed bug, not something to
    paper over.

    The walk stops at the first level whose price is worse than
    `max_slippage_bps` off the top of book; levels beyond that are not
    reachable within the caller's slippage budget and are not counted.
    An empty book scores `0` (no venue outage handling here -- that is
    `EM_NO_ROUTE`'s job upstream). The result is capped at `1` even if
    the book holds more than `desired_qty` within budget.
    """
    if desired_qty <= 0:
        raise ValueError("depth_absorption_score: desired_qty must be > 0.")
    if max_slippage_bps < 0:
        raise ValueError("depth_absorption_score: max_slippage_bps must be >= 0.")
    if not levels:
        return Decimal("0")

    prices = [level.price for level in levels]
    if side == "BUY":
        if prices != sorted(prices):
            raise ValueError(
                "depth_absorption_score: BUY walks asks, which must be sorted "
                "ascending (best/lowest ask first)."
            )
    else:
        if prices != sorted(prices, reverse=True):
            raise ValueError(
                "depth_absorption_score: SELL walks bids, which must be sorted "
                "descending (best/highest bid first)."
            )

    best_price = levels[0].price
    slippage_factor = max_slippage_bps / Decimal(10000)
    price_limit = (
        best_price * (1 + slippage_factor) if side == "BUY" else best_price * (1 - slippage_factor)
    )

    absorbed = Decimal("0")
    for level in levels:
        within_budget = level.price <= price_limit if side == "BUY" else level.price >= price_limit
        if not within_budget:
            break
        absorbed += level.size
        if absorbed >= desired_qty:
            break

    score = absorbed / desired_qty
    return min(score, Decimal("1"))
