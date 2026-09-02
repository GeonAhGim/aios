"""LB-3 — WeightedAverage 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3,
`unit/positions/test_weighted.py` DoD("평단 재계산, 매도 시 평단 불변").
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.cost_basis.weighted import FillEvent, WeightedAverage


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _fill(side: OrderSide, quantity: str, price: str) -> FillEvent:
    return FillEvent(
        side=side, quantity=Decimal(quantity), price=Decimal(price), occurred_at=_now()
    )


def test_first_buy_sets_avg_cost_to_fill_price() -> None:
    wavg = WeightedAverage()
    result = wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    assert result.realized_pnl == Decimal("0")
    assert len(result.lots) == 1
    assert result.lots[0].quantity == Decimal("10")
    assert result.lots[0].unit_cost == Decimal("100")


def test_second_buy_recomputes_weighted_average_cost() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    result = wavg.apply(_fill(OrderSide.BUY, "10", "110"))

    # (10*100 + 10*110) / 20 = 105
    assert result.lots[0].quantity == Decimal("20")
    assert result.lots[0].unit_cost == Decimal("105.0000000000")


def test_sell_keeps_avg_cost_unchanged_and_computes_realized_pnl() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    wavg.apply(_fill(OrderSide.BUY, "10", "110"))

    result = wavg.apply(_fill(OrderSide.SELL, "5", "120"))

    # 평단은 105로 유지, 실현손익 = (120-105)*5 = 75
    assert result.realized_pnl == Decimal("75")
    assert result.lots[0].quantity == Decimal("15")
    assert result.lots[0].unit_cost == Decimal("105.0000000000")


def test_sell_closing_full_position_leaves_no_lots() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    result = wavg.apply(_fill(OrderSide.SELL, "10", "120"))

    assert result.realized_pnl == Decimal("200")
    assert result.lots == ()


def test_buy_after_full_close_resets_avg_cost() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))
    wavg.apply(_fill(OrderSide.SELL, "10", "120"))

    result = wavg.apply(_fill(OrderSide.BUY, "5", "90"))

    assert result.lots[0].quantity == Decimal("5")
    assert result.lots[0].unit_cost == Decimal("90.0000000000")


def test_sell_exceeding_available_quantity_rejected() -> None:
    wavg = WeightedAverage()
    wavg.apply(_fill(OrderSide.BUY, "10", "100"))

    with pytest.raises(NegativeQuantityError):
        wavg.apply(_fill(OrderSide.SELL, "11", "120"))

    # 거부된 매도는 상태를 바꾸지 않는다.
    assert wavg.lots[0].quantity == Decimal("10")


def test_sell_with_no_position_rejected() -> None:
    wavg = WeightedAverage()
    with pytest.raises(NegativeQuantityError):
        wavg.apply(_fill(OrderSide.SELL, "1", "100"))
