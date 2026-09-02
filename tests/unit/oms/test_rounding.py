"""tick/lot/min-notional 라운딩 단위테스트 — L4-03. DB 없음."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.rounding import check_notional, round_price, round_qty


def test_round_price_buy_rounds_down() -> None:
    """불리한 방향 금지 — BUY는 의도보다 비싸게 사지 않도록 내림."""
    result = round_price(Decimal("100.07"), Decimal("0.05"), OrderSide.BUY)
    assert result == Decimal("100.05")


def test_round_price_sell_rounds_up() -> None:
    """SELL은 의도보다 싸게 팔지 않도록 올림."""
    result = round_price(Decimal("100.07"), Decimal("0.05"), OrderSide.SELL)
    assert result == Decimal("100.10")


def test_round_price_already_on_tick_is_unchanged() -> None:
    assert round_price(Decimal("100.05"), Decimal("0.05"), OrderSide.BUY) == Decimal("100.05")
    assert round_price(Decimal("100.05"), Decimal("0.05"), OrderSide.SELL) == Decimal("100.05")


def test_round_price_zero_tick_returns_original() -> None:
    assert round_price(Decimal("100.07"), Decimal("0"), OrderSide.BUY) == Decimal("100.07")


def test_round_qty_always_rounds_down() -> None:
    assert round_qty(Decimal("1.237"), Decimal("0.01")) == Decimal("1.23")


def test_round_qty_zero_lot_returns_original() -> None:
    assert round_qty(Decimal("1.237"), Decimal("0")) == Decimal("1.237")


def test_round_qty_never_rounds_up() -> None:
    """수량은 side와 무관하게 항상 내림 — 자본 초과배분 방지."""
    result = round_qty(Decimal("0.999"), Decimal("0.1"))
    assert result == Decimal("0.9")
    assert result <= Decimal("0.999")


def test_check_notional_passes_when_above_minimum() -> None:
    check_notional(Decimal("100"), Decimal("1"), Decimal("50"))  # 예외 없이 통과


def test_check_notional_rejects_below_minimum() -> None:
    with pytest.raises(OrderValidationError) as exc_info:
        check_notional(Decimal("10"), Decimal("1"), Decimal("50"))
    assert exc_info.value.code == "OMS_VALIDATION_MIN_NOTIONAL"


def test_check_notional_zero_minimum_skips_check() -> None:
    check_notional(Decimal("0.01"), Decimal("0.001"), Decimal("0"))  # 예외 없이 통과
