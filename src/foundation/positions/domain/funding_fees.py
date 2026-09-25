"""LB-4 — Funding fees and commission (funding_fees).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

Funding for perpetual contracts follows "position sign × notional × rate"
(§2.3 module table). `qty` is a signed quantity (long=positive, short=negative),
and `notional = |qty| × mark`, so `sign(qty) × notional == qty` holds,
allowing `funding = qty × mark.amount × rate`. The sign convention (long pays
when rate is positive) has not yet been cross-validated against exchange
documentation — **unverified**, always reconcile actual pay/receive direction
against real exchange (Bitget) funding settlements.

Conversion of commission and funding fees to the base currency is delegated
to [[fx.convert]] — if the rate is missing or stale, an exception propagates
(no silent fallback), using the same taxonomy as [[fx]] (`POS_FX_RATE_MISSING`).
Pure functions only — no direct I/O or clock calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.domain import fx


def funding_amount(qty: Decimal, mark: Money, rate: Decimal) -> Money:
    """Funding pay/receive amount for a perpetual position. Sign follows `qty` —
    assumes the convention that long (positive) pays (negative cash flow) when
    `rate` is positive (unverified)."""
    return Money(amount=qty * mark.amount * rate, currency=mark.currency)


def to_base(
    amount: Money | None,
    base_currency: Currency,
    rate: FXRate | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = fx.DEFAULT_MAX_RATE_AGE,
) -> Decimal:
    """Convert commission (`fee`) or funding payment (result of [[funding_amount]])
    to base currency as `Decimal`. Returns 0 when `amount` is `None` (no-fee
    fill) — the only case where the rate is not looked up.
    """
    if amount is None:
        return Decimal("0")
    return fx.convert(amount, base_currency, rate, now=now, max_age=max_age).amount
