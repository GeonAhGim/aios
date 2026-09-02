"""tick/lot/min-notional 라운딩(L4 명세 §2-A, R7).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9 L4-03.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_UP, Decimal

from src.data.models.trading import OrderSide
from src.services.oms.domain.errors import OrderValidationError


def round_price(price: Decimal, tick: Decimal, side: OrderSide) -> Decimal:
    """가격을 tick 단위로 맞춘다 — "불리한 방향 금지": BUY는 내림(더 낮은
    가격, 의도보다 비싸게 사지 않음), SELL은 올림(더 높은 가격, 의도보다
    싸게 팔지 않음). `tick`이 0이면(시장가 등 tick 불필요) 원본을
    그대로 반환한다."""
    if tick <= 0:
        return price
    rounding = ROUND_DOWN if side == OrderSide.BUY else ROUND_UP
    units = (price / tick).to_integral_value(rounding=rounding)
    return units * tick


def round_qty(qty: Decimal, lot: Decimal) -> Decimal:
    """수량은 항상 내림(lot 단위) — side와 무관하게 승인된 자본을 초과
    배분하지 않는다."""
    if lot <= 0:
        return qty
    units = (qty / lot).to_integral_value(rounding=ROUND_DOWN)
    return units * lot


def check_notional(price: Decimal, qty: Decimal, min_notional: Decimal) -> None:
    """§2-A — 위반 시 `OrderValidationError("MIN_NOTIONAL")`. `min_notional`이
    0 이하면 검사 대상이 아니다(해당 venue/심볼에 하한이 없음)."""
    if min_notional <= 0:
        return
    notional = price * qty
    if notional < min_notional:
        raise OrderValidationError(
            "MIN_NOTIONAL",
            f"주문가치({notional})가 최소주문금액({min_notional}) 미만입니다.",
        )
