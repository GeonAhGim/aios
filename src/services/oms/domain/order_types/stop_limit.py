"""Stop-limit order — trigger determination + post-trigger order shape
decision (pure) (L4 spec §9 EM-19).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md §9 EM-19.

A plain stop, once triggered, goes out as a market order and is defenseless
against slippage. A stop-limit uses the same trigger condition as a stop
(reuses `stop.is_stop_triggered`), but once triggered converts to a limit
order that caps/floors the fill price (in exchange for taking on the risk
of not filling). The exact gap (offset) convention between trigger price
and limit price differs per exchange — this only validates direction
(protective vs. adverse) and does not enforce the width (unverified:
requires cross-checking against actual exchange documentation).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide, OrderType
from src.services.oms.domain.order_types.stop import is_stop_triggered

__all__ = [
    "InvalidStopLimitPriceError",
    "TriggeredOrderSpec",
    "validate_stop_limit_prices",
    "is_stop_limit_triggered",
    "resolve_triggered_order",
]


class InvalidStopLimitPriceError(ValueError):
    """A trigger-price/limit-price direction combination that is not
    protective — this would become a dead order that can never fill the
    instant it triggers, so it is rejected fail-closed at submission time."""


@dataclass(frozen=True, slots=True)
class TriggeredOrderSpec:
    order_type: OrderType
    price: Decimal


def validate_stop_limit_prices(
    *, side: OrderSide, trigger_price: Decimal, limit_price: Decimal
) -> None:
    """Direction is protective only if a buy stop-limit has limit_price >=
    trigger_price (a ceiling allowing chase-buying right after breakout),
    and a sell stop-limit has limit_price <= trigger_price (a floor
    allowing slippage right after a stop-loss)."""
    if side == OrderSide.BUY and limit_price < trigger_price:
        raise InvalidStopLimitPriceError(
            f"매수 스탑리밋은 limit_price({limit_price}) >= "
            f"trigger_price({trigger_price})여야 합니다."
        )
    if side == OrderSide.SELL and limit_price > trigger_price:
        raise InvalidStopLimitPriceError(
            f"매도 스탑리밋은 limit_price({limit_price}) <= "
            f"trigger_price({trigger_price})여야 합니다."
        )


def is_stop_limit_triggered(
    *, side: OrderSide, trigger_price: Decimal, last_price: Decimal
) -> bool:
    return is_stop_triggered(side=side, trigger_price=trigger_price, last_price=last_price)


def resolve_triggered_order(*, limit_price: Decimal) -> TriggeredOrderSpec:
    """The order shape actually sent to the venue after trigger —
    LIMIT@limit_price."""
    return TriggeredOrderSpec(order_type=OrderType.LIMIT, price=limit_price)
