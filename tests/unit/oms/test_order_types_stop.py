"""스톱 주문 트리거 판정 단위테스트 — EM-19. DB 없음.

결정 틱 리플레이 픽스처: 고정된 틱 시퀀스를 재생해 트리거가 정확히
트리거가에서(경계 포함) 발동하고 그 이전 틱에서는 발동하지 않음을
증명한다."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.services.oms.domain.order_types.stop import is_stop_triggered

_BUY_TICK_REPLAY = [
    (Decimal("99.00"), False),
    (Decimal("99.99"), False),
    (Decimal("100.00"), True),  # 정확히 트리거가
    (Decimal("100.01"), True),
    (Decimal("105.00"), True),
]

_SELL_TICK_REPLAY = [
    (Decimal("101.00"), False),
    (Decimal("100.01"), False),
    (Decimal("100.00"), True),  # 정확히 트리거가
    (Decimal("99.99"), True),
    (Decimal("95.00"), True),
]


def test_buy_stop_tick_replay_triggers_exactly_at_trigger_price() -> None:
    trigger_price = Decimal("100.00")
    for last_price, expected in _BUY_TICK_REPLAY:
        actual = is_stop_triggered(
            side=OrderSide.BUY, trigger_price=trigger_price, last_price=last_price
        )
        assert actual is expected, f"last_price={last_price}: expected {expected}, got {actual}"


def test_sell_stop_tick_replay_triggers_exactly_at_trigger_price() -> None:
    trigger_price = Decimal("100.00")
    for last_price, expected in _SELL_TICK_REPLAY:
        actual = is_stop_triggered(
            side=OrderSide.SELL, trigger_price=trigger_price, last_price=last_price
        )
        assert actual is expected, f"last_price={last_price}: expected {expected}, got {actual}"


def test_buy_stop_does_not_trigger_below_trigger_price() -> None:
    triggered = is_stop_triggered(
        side=OrderSide.BUY, trigger_price=Decimal("100"), last_price=Decimal("99.9999")
    )
    assert triggered is False


def test_sell_stop_does_not_trigger_above_trigger_price() -> None:
    triggered = is_stop_triggered(
        side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("100.0001")
    )
    assert triggered is False


def test_rejects_non_positive_trigger_price() -> None:
    with pytest.raises(ValueError, match="trigger_price"):
        is_stop_triggered(side=OrderSide.BUY, trigger_price=Decimal("0"), last_price=Decimal("1"))


def test_rejects_non_positive_last_price() -> None:
    with pytest.raises(ValueError, match="last_price"):
        is_stop_triggered(
            side=OrderSide.SELL, trigger_price=Decimal("100"), last_price=Decimal("-1")
        )
