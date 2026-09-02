"""LB-2 — FifoLots 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-2,
`unit/positions/test_fifo.py` DoD("매수 10@100, 5@110, 매도 12 → 실현
(12: 10@100+2@110), 로트 잔량 3@110; 초과 매도 → `POS_NEGATIVE_QUANTITY`;
JSON 왕복").
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.domain.cost_basis.fifo import (
    FifoLots,
    FillEvent,
    NegativeQuantityError,
)


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _fill(side: OrderSide, quantity: str, price: str) -> FillEvent:
    return FillEvent(
        side=side, quantity=Decimal(quantity), price=Decimal(price), occurred_at=_now()
    )


def test_buy_pushes_lot_with_zero_realized_pnl() -> None:
    lots = FifoLots()
    result = lots.apply(_fill(OrderSide.BUY, "10", "100"))
    assert result.realized_pnl == Decimal("0")
    assert lots.lots == result.lots
    assert [(lot.quantity, lot.unit_cost) for lot in lots.lots] == [(Decimal("10"), Decimal("100"))]


def test_sell_consumes_head_first_and_computes_realized_pnl() -> None:
    lots = FifoLots()
    lots.apply(_fill(OrderSide.BUY, "10", "100"))
    lots.apply(_fill(OrderSide.BUY, "5", "110"))

    result = lots.apply(_fill(OrderSide.SELL, "12", "120"))

    # 매도 12: 10@100 전량 소진 + 2@110 부분 소진.
    expected_realized = (Decimal("120") - Decimal("100")) * Decimal("10") + (
        Decimal("120") - Decimal("110")
    ) * Decimal("2")
    assert result.realized_pnl == expected_realized

    remaining = lots.lots
    assert len(remaining) == 1
    assert remaining[0].quantity == Decimal("3")
    assert remaining[0].unit_cost == Decimal("110")


def test_sell_exceeding_available_quantity_rejected() -> None:
    lots = FifoLots()
    lots.apply(_fill(OrderSide.BUY, "10", "100"))

    with pytest.raises(NegativeQuantityError):
        lots.apply(_fill(OrderSide.SELL, "11", "120"))

    # 거부된 매도는 상태를 바꾸지 않는다.
    assert lots.lots[0].quantity == Decimal("10")


def test_sell_with_no_lots_rejected() -> None:
    lots = FifoLots()
    with pytest.raises(NegativeQuantityError):
        lots.apply(_fill(OrderSide.SELL, "1", "100"))


def test_to_json_from_json_round_trips() -> None:
    lots = FifoLots()
    lots.apply(_fill(OrderSide.BUY, "10", "100"))
    lots.apply(_fill(OrderSide.BUY, "5", "110"))

    payload = lots.to_json()
    restored = FifoLots.from_json(payload)

    assert restored.lots == lots.lots
    assert restored.to_json() == payload


def test_fill_event_rejects_naive_occurred_at() -> None:
    with pytest.raises(ValueError):
        FillEvent(
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            occurred_at=datetime(2026, 9, 3, 0, 0),
        )


def test_fill_event_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError):
        _fill(OrderSide.BUY, "0", "100")
