"""5.7 — Validator.validate_order_params().

Spec: 03_core_modules_v1.1.md#§3.3, 08_test_plan_v1.2.md#§8.2

6.7 원칙 — 시스템 전체의 기본 방어선. 정책 위반 여부(Risk 한도 등)는
FROZEN Zone(Risk Engine)의 책임이며, 여기서는 순수 데이터 형식·필수값
검증만 수행한다.

편차: tick_size/supported_asset_classes는 각각 ExchangeAdapter(작업트리
6번, 아직 미구현)가 제공할 값이라 콜러가 주입하는 선택 파라미터로 두었다
— 주어지지 않으면 해당 검사는 건너뛴다(값을 몰라서 통과시키는 것이지,
검증 자체를 생략해도 안전하다는 뜻은 아니다. Adapter 착수 후 항상 채워
호출해야 한다).
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

    # ADR-2026-08-28 capability-gated 원칙(02번 §2.0-A) — 대상 거래소가
    # 지원하지 않는 자산군이면 즉시 거부(침묵 실패/임의 폴백 금지).
    if supported_asset_classes is not None and order.asset_class not in supported_asset_classes:
        errors.append(f"UNSUPPORTED_ASSET_CLASS: {order.asset_class.value}")

    return ValidationResult(is_valid=not errors, errors=errors)
