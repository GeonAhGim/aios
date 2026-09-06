"""스톱리밋 주문 단위테스트 — EM-19. DB 없음."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide, OrderType
from src.services.oms.domain.order_types.stop_limit import (
    InvalidStopLimitPriceError,
    is_stop_limit_triggered,
    resolve_triggered_order,
    validate_stop_limit_prices,
)


def test_buy_stop_limit_accepts_limit_at_or_above_trigger() -> None:
    validate_stop_limit_prices(
        side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100.5")
    )
    validate_stop_limit_prices(
        side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("100")
    )


def test_buy_stop_limit_rejects_limit_below_trigger() -> None:
    with pytest.raises(InvalidStopLimitPriceError):
        validate_stop_limit_prices(
            side=OrderSide.BUY, trigger_price=Decimal("100"), limit_price=Decimal("99.99")
        )


def test_sell_stop_limit_accepts_limit_at_or_below_trigger() -> None:
    validate_stop_limit_prices(
        side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("99.5")
    )
    validate_stop_limit_prices(
        side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("100")
    )


def test_sell_stop_limit_rejects_limit_above_trigger() -> None:
    with pytest.raises(InvalidStopLimitPriceError):
        validate_stop_limit_prices(
            side=OrderSide.SELL, trigger_price=Decimal("100"), limit_price=Decimal("100.01")
        )


def test_stop_limit_trigger_condition_matches_stop() -> None:
    triggered = is_stop_limit_triggered(
        side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("100")
    )
    assert triggered is True
    not_triggered = is_stop_limit_triggered(
        side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("100.01")
    )
    assert not_triggered is False


def test_resolve_triggered_order_becomes_limit_at_limit_price() -> None:
    spec = resolve_triggered_order(limit_price=Decimal("99.5"))
    assert spec.order_type is OrderType.LIMIT
    assert spec.price == Decimal("99.5")
