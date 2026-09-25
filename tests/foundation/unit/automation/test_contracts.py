from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.trading import OrderSide
from src.foundation.automation.contracts.v1 import HedgeAction, OrderAction, TimeCondition


def test_order_action_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValidationError, match="quantity"):
        OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("0"))


def test_hedge_action_rejects_negative_quantity() -> None:
    with pytest.raises(ValidationError, match="quantity"):
        HedgeAction(symbol="005930", hedge_symbol="KOSPI200F", quantity=Decimal("-1"))


def test_time_condition_rejects_out_of_range_weekday() -> None:
    from datetime import time

    with pytest.raises(ValidationError, match="days_of_week"):
        TimeCondition(at=time(9, 0), days_of_week=(7,))
