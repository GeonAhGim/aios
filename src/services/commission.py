"""13.7 — P2P 중개수수료 계산.

Spec: 기능설계문서_v1.20.md#FD-13.7, docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-2.

Core distinction (2026-08-10 confirmed): This is not a performance fee tied to
trading results (excluded from capital markets law investment advisory licensing
issues) — it is a platform intermediary fee collected at a fixed ratio of the
selling price whenever a "strategy sale transaction" is completed (same structure
as an App Store transaction fee). Subsequent P&L of the strategy has no impact
on this calculation.

DEFAULT_COMMISSION_RATE is a Draft (midpoint of 10~20% range) — a reasonable
default value to start with; a business review will finalize it.

Rounding (sum-preserving) is delegated to
`domain/rounding.split_commission` (LC-2, R3): previously `price x rate` was
returned without rounding, and when stored in the DB `NUMERIC(20,2)` column the
DB would round, potentially causing commission + payout != price.
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.ledger.domain.rounding import split_commission

DEFAULT_COMMISSION_RATE = Decimal("0.15")  # Draft — midpoint of 10~20% range


class CommissionError(ValueError):
    """Invalid commission input (negative price, out-of-range rate, etc.)."""


def _validate_rate(rate: Decimal) -> None:
    """Reject rates outside the valid [0, 1] range or non-finite values."""
    if rate.is_nan() or rate.is_infinite():
        raise CommissionError(f"Rate must be finite: {rate!r}")
    if rate < 0 or rate > 1:
        raise CommissionError(f"Rate must be in [0, 1]: {rate!r}")


def _validate_price(price: Decimal) -> None:
    """Reject negative prices — a commission on a negative amount is meaningless."""
    if price < 0:
        raise CommissionError(f"Price must be non-negative: {price!r}")


def calculate_commission(
    price_paid: Decimal | None, rate: Decimal = DEFAULT_COMMISSION_RATE
) -> tuple[Decimal | None, Decimal | None]:
    """Return (platform_commission_amount, seller_payout_amount).

    Raises ``CommissionError`` for invalid inputs (negative price, rate outside
    ``[0, 1]``, non-finite rate).  Returns ``(None, None)`` when *price_paid* is
    ``None`` (free listing has nothing to calculate commission on).
    """
    if price_paid is None:
        return None, None
    _validate_price(price_paid)
    _validate_rate(rate)
    return split_commission(price_paid, rate)
