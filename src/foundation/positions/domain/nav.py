"""LB-6 — Daily NAV chain.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-6,
§4.3 "prior NAV + PnL + cash flow = current NAV", DB `CHECK(closing_nav = cash +
positions_mv)`.

Two independent equations must hold simultaneously for a day's NAV to be valid:

1. Balance-sheet equation (balance basis) — `closing = cash + Σ position_mv`.
   [[compute_daily_nav]] computes `closing_nav` directly via this formula, so
   the returned `NAVSnapshot` always satisfies this equation by construction
   (no separate verification needed).
2. Roll-forward equation (PnL basis) — `closing = opening + realized +
   Δunrealized + funding − fees + flows`, and `opening` must equal the
   prior day's `closing` for the chain to continue. [[verify_chain]] receives
   two consecutive snapshots and checks both conditions.

If the two equations diverge (exact `Decimal` equality comparison, not rounded)
`POS_NAV_CHAIN_BROKEN` (cannot retry until operational intervention) — this
signals that the write must be rejected. Pure functions only — no I/O or
direct clock calls.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from src.data.models.base import Currency, FXRate
from src.foundation.positions.contracts.v1 import NAVSnapshot, PositionErrorCode


class NavChainBrokenError(Exception):
    """`POS_NAV_CHAIN_BROKEN` — NAV chain equation does not hold (impossible
    state, requires operational intervention). Tolerance 0 — never round
    values to force the equation to balance."""

    code = PositionErrorCode.NAV_CHAIN_BROKEN

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NavInputs:
    """Inputs for [[compute_daily_nav]]. `position_mvs` are the mark-to-market
    values of each position opened that day in base currency, and
    `Σ position_mvs` forms the right-hand side of the balance-sheet equation."""

    account_id: UUID
    nav_date: date
    base_currency: Currency
    opening_nav: Decimal
    cash: Decimal
    position_mvs: Sequence[Decimal]
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal
    fx_rates: Sequence[FXRate] = field(default_factory=tuple)


def compute_daily_nav(inputs: NavInputs) -> NAVSnapshot:
    """Compute `closing_nav` via the balance-sheet equation and build an
    `NAVSnapshot`.

    `source_hash` is a deterministic hash of all input components — if the
    `source_hash` of a re-computed result differs from the already-stored row
    identified by `(account_id, nav_date)`, the caller (LB-15
    `compute_daily_nav` application) rejects the overwrite (§8
    "different source_hash from existing row → POS_NAV_CHAIN_BROKEN").
    """
    positions_mv = sum(inputs.position_mvs, Decimal("0"))
    closing_nav = inputs.cash + positions_mv

    return NAVSnapshot(
        account_id=inputs.account_id,
        nav_date=inputs.nav_date,
        base_currency=inputs.base_currency,
        opening_nav=inputs.opening_nav,
        cash=inputs.cash,
        positions_mv=positions_mv,
        realized=inputs.realized,
        unrealized_delta=inputs.unrealized_delta,
        funding=inputs.funding,
        fees=inputs.fees,
        flows=inputs.flows,
        closing_nav=closing_nav,
        fx_rates=list(inputs.fx_rates),
        source_hash=_source_hash(inputs, positions_mv=positions_mv, closing_nav=closing_nav),
    )


def verify_chain(prev: NAVSnapshot, cur: NAVSnapshot) -> None:
    """Verify the roll-forward equation from `prev`(prior day) → `cur`(current
    day) with tolerance 0.

    - Continuity: `cur.opening_nav == prev.closing_nav`.
    - Roll-forward: `cur.closing_nav == cur.opening_nav + cur.realized +
      cur.unrealized_delta + cur.funding − cur.fees + cur.flows`.

    Raises `NavChainBrokenError` if either condition fails — never absorb
    the difference via approximate comparison or quantize.
    """
    if cur.opening_nav != prev.closing_nav:
        raise NavChainBrokenError(
            f"{cur.account_id}/{cur.nav_date}: opening_nav={cur.opening_nav} != "
            f"prev.closing_nav={prev.closing_nav}"
        )

    expected_closing = (
        cur.opening_nav
        + cur.realized
        + cur.unrealized_delta
        + cur.funding
        - cur.fees
        + cur.flows
    )
    if cur.closing_nav != expected_closing:
        raise NavChainBrokenError(
            f"{cur.account_id}/{cur.nav_date}: closing_nav={cur.closing_nav} != "
            f"expected(opening+realized+Δunrealized+funding-fees+flows)={expected_closing}"
        )


def _source_hash(inputs: NavInputs, *, positions_mv: Decimal, closing_nav: Decimal) -> str:
    payload = json.dumps(
        {
            "account_id": str(inputs.account_id),
            "nav_date": inputs.nav_date.isoformat(),
            "base_currency": inputs.base_currency.value,
            "opening_nav": str(inputs.opening_nav),
            "cash": str(inputs.cash),
            "positions_mv": str(positions_mv),
            "realized": str(inputs.realized),
            "unrealized_delta": str(inputs.unrealized_delta),
            "funding": str(inputs.funding),
            "fees": str(inputs.fees),
            "flows": str(inputs.flows),
            "closing_nav": str(closing_nav),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
