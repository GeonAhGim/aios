"""트레일링 스톱 단위테스트 — EM-19. DB 없음.

DoD: 트리거가가 단조 갱신된다 — 시퀀스 전체에서 SELL은 절대 감소하지
않고, BUY는 절대 증가하지 않는다(가격이 되돌아가도 트리거가는 그대로거나
더 유리해질 뿐 후퇴하지 않는다)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.services.oms.domain.order_types.trailing import (
    initial_trailing_state,
    is_trailing_triggered,
    update_trailing_stop,
)


def test_sell_trailing_trigger_price_is_monotonic_non_decreasing() -> None:
    state = initial_trailing_state(
        side=OrderSide.SELL, reference_price=Decimal("100"), trailing_offset=Decimal("5")
    )
    assert state.trigger_price == Decimal("95")

    tick_sequence = [
        Decimal("102"),
        Decimal("110"),  # 신고가 — 트리거가 105로 상향
        Decimal("108"),  # 되돌림 — 트리거가는 그대로(후퇴 금지)
        Decimal("103"),  # 추가 되돌림 — 여전히 그대로
        Decimal("120"),  # 재신고가 — 트리거가 115로 상향
    ]
    prev_trigger = state.trigger_price
    for last_price in tick_sequence:
        state = update_trailing_stop(
            side=OrderSide.SELL, state=state, last_price=last_price, trailing_offset=Decimal("5")
        )
        assert state.trigger_price >= prev_trigger
        prev_trigger = state.trigger_price
    assert state.trigger_price == Decimal("115")


def test_buy_trailing_trigger_price_is_monotonic_non_increasing() -> None:
    state = initial_trailing_state(
        side=OrderSide.BUY, reference_price=Decimal("100"), trailing_offset=Decimal("5")
    )
    assert state.trigger_price == Decimal("105")

    tick_sequence = [
        Decimal("98"),
        Decimal("90"),  # 신저가 — 트리거가 95로 하향
        Decimal("93"),  # 되돌림 — 트리거가는 그대로(후퇴 금지)
        Decimal("80"),  # 재신저가 — 트리거가 85로 하향
    ]
    prev_trigger = state.trigger_price
    for last_price in tick_sequence:
        state = update_trailing_stop(
            side=OrderSide.BUY, state=state, last_price=last_price, trailing_offset=Decimal("5")
        )
        assert state.trigger_price <= prev_trigger
        prev_trigger = state.trigger_price
    assert state.trigger_price == Decimal("85")


def test_sell_trailing_triggers_when_price_falls_to_trigger() -> None:
    state = initial_trailing_state(
        side=OrderSide.SELL, reference_price=Decimal("100"), trailing_offset=Decimal("5")
    )
    state = update_trailing_stop(
        side=OrderSide.SELL, state=state, last_price=Decimal("110"), trailing_offset=Decimal("5")
    )
    assert state.trigger_price == Decimal("105")
    at_trigger = is_trailing_triggered(side=OrderSide.SELL, state=state, last_price=Decimal("105"))
    assert at_trigger is True
    above_trigger = is_trailing_triggered(
        side=OrderSide.SELL, state=state, last_price=Decimal("105.01")
    )
    assert above_trigger is False


def test_rejects_non_positive_trailing_offset() -> None:
    with pytest.raises(ValueError, match="trailing_offset"):
        initial_trailing_state(
            side=OrderSide.SELL, reference_price=Decimal("100"), trailing_offset=Decimal("0")
        )
