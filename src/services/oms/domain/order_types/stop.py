"""Stop order trigger determination (pure) (L4 spec §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19,
ADR-2026-09-06-G §9 ("order types are MARKET/LIMIT only — no stop/stop-
limit/OCO/trailing. EMSX/Charles River treat these as basic order types,
not algorithms").

The actual fill price after triggering is not this function's
responsibility (venue submission / fill_normalizer.py) — this module only
determines "did the trigger condition hold on this tick". The isomorphic
determination on the backtest side (`foundation/backtest/domain/fill/
order_types.py` BT-6) uses the bar's high/low (bar_low/bar_high), but live
only ever has a single price per tick (last_price), so the same inequality
is rewritten on a per-tick basis — the two modules are independent
determinations in different layers (backtest vs. live) and do not import
each other.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.trading import OrderSide


def _reject_non_positive(value: Decimal, name: str) -> None:
    if value.is_nan() or value <= 0:
        raise ValueError(f"{name}는 0보다 커야 합니다: {value}")


def is_stop_triggered(*, side: OrderSide, trigger_price: Decimal, last_price: Decimal) -> bool:
    """A buy stop (breakout buy / short cover) fires when the last price
    rises to or above the trigger price; a sell stop (stop-loss) fires when
    it falls to or below the trigger price — inclusive of the boundary, so
    a tick that lands exactly on the trigger price also triggers."""
    _reject_non_positive(trigger_price, "trigger_price")
    _reject_non_positive(last_price, "last_price")
    if side == OrderSide.BUY:
        return last_price >= trigger_price
    return last_price <= trigger_price
