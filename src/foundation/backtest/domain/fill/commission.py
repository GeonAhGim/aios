"""BT-3 — Commission model (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-3, §3.4(`commission: VenueTier{venue, maker_bps, taker_bps, min_fee}`).

Consumes `VenueTierCommission` (`domain/models_v2.py`) as-is. The fixed
floor (`min_fee`) applies ceiling when the rate-based fee falls below it
(not floor), reflecting the minimum fee exchanges actually charge on
small trades.
"""
from __future__ import annotations

from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import VenueTierCommission

_BPS = Decimal("10000")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}: negative and NaN values are not allowed: {value}")


def compute_commission(model: VenueTierCommission, *, is_maker: bool, notional: Decimal) -> Decimal:
    """Apply tier-specific bps to trade notional (`notional` = price * quantity)
    and enforce the `min_fee` floor."""

    _reject_negative_or_nan(notional, "notional")
    rate_bps = model.maker_bps if is_maker else model.taker_bps
    fee = notional * rate_bps / _BPS
    return max(fee, model.min_fee)
