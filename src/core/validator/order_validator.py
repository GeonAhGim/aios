"""5.7 — Validator.validate_order_params().

Spec: 03_core_modules_v1.1.md#§3.3, 08_test_plan_v1.2.md#§8.2

Principle 6.7 — First line of defense across the system. Policy violations
(e.g. Risk limits) are the responsibility of the FROZEN Zone (Risk Engine);
this module performs only pure data format and required-field validation.

Deviation: tick_size and supported_asset_classes are optional parameters
injected by the caller, since their values will be provided by ExchangeAdapter
(Task #6, not yet implemented). If not provided, the respective check is
skipped — this does not mean it is safe to omit validation; always populate
these values after the Adapter is implemented and call accordingly.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.validator.result import ValidationResult
from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderType


def validate_order_params(
    order: Order,
    *,
    tick_size: Decimal | None = None,
    supported_asset_classes: list[AssetClass] | None = None,
) -> ValidationResult:
    errors: list[str] = []

    if order.quantity <= 0:
        errors.append("수량은 0보다 커야 합니다.")

    if order.order_type == OrderType.LIMIT and order.price is None:
        errors.append("LIMIT 주문은 price가 필요합니다.")
    if order.order_type == OrderType.MARKET and order.price is not None:
        errors.append("MARKET 주문은 price를 지정할 수 없습니다.")

    if tick_size is not None and order.price is not None and tick_size > 0:
        if (order.price.amount % tick_size) != 0:
            errors.append(f"가격이 tick_size({tick_size})의 배수가 아닙니다.")

    # ADR-2026-08-28 capability-gated principle (#02 §2.0-A) — reject
    # immediately if the target exchange does not support the asset class
    # (silence failure or arbitrary fallback prohibited).
    if supported_asset_classes is not None and order.asset_class not in supported_asset_classes:
        errors.append(f"UNSUPPORTED_ASSET_CLASS: {order.asset_class.value}")

    return ValidationResult(is_valid=not errors, errors=errors)
