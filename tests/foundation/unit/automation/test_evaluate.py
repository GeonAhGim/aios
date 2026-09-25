from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from src.foundation.automation.contracts.v1 import (
    DisclosureCondition,
    IndicatorCondition,
    PriceCondition,
    PriceField,
    TimeCondition,
)
from src.foundation.automation.domain.evaluate import (
    MarketSnapshot,
    evaluate_condition,
    evaluate_conditions,
)

from .conftest import make_candle


def test_price_condition_gt_triggers() -> None:
    condition = PriceCondition(
        symbol="005930", field=PriceField.CLOSE, operator=">", threshold=Decimal("100")
    )
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("101")))
    assert evaluate_condition(condition, {"005930": snapshot}, {}) is True


def test_price_condition_crosses_above_requires_prev() -> None:
    condition = PriceCondition(operator="crosses_above", threshold=Decimal("100"), symbol="005930")
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("101")))
    # 이전 스냅샷 없음 -> crosses_above는 발동하지 않는다(결손을 "발동"으로 읽지 않음).
    assert evaluate_condition(condition, {"005930": snapshot}, {}) is False
    prev = MarketSnapshot(candle=make_candle(close=Decimal("99")))
    assert evaluate_condition(condition, {"005930": snapshot}, {"005930": prev}) is True


def test_indicator_condition_missing_data_is_fail_closed() -> None:
    condition = IndicatorCondition(
        symbol="005930", indicator="rsi14", operator=">", threshold=Decimal("70")
    )
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("100")), indicators={})
    assert evaluate_condition(condition, {"005930": snapshot}, {}) is False


def test_indicator_condition_present_triggers() -> None:
    condition = IndicatorCondition(
        symbol="005930", indicator="rsi14", operator=">", threshold=Decimal("70")
    )
    snapshot = MarketSnapshot(
        candle=make_candle(close=Decimal("100")), indicators={"rsi14": Decimal("75")}
    )
    assert evaluate_condition(condition, {"005930": snapshot}, {}) is True


def test_disclosure_condition() -> None:
    condition = DisclosureCondition(symbol="005930", filing_type="EARNINGS")
    present = MarketSnapshot(
        candle=make_candle(close=Decimal("100")), disclosures=frozenset({"EARNINGS"})
    )
    absent = MarketSnapshot(candle=make_candle(close=Decimal("100")), disclosures=frozenset())
    assert evaluate_condition(condition, {"005930": present}, {}) is True
    assert evaluate_condition(condition, {"005930": absent}, {}) is False


def test_time_condition_matches_time_and_weekday() -> None:
    monday = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)  # 2026-01-05는 월요일
    condition = TimeCondition(at=time(9, 0), days_of_week=(0,))
    snapshot = MarketSnapshot(
        candle=make_candle(close=Decimal("100")).model_copy(update={"open_time": monday})
    )
    assert evaluate_condition(condition, {"005930": snapshot}, {}) is True

    tuesday = datetime(2026, 1, 6, 9, 0, tzinfo=timezone.utc)
    snapshot2 = MarketSnapshot(
        candle=make_candle(close=Decimal("100")).model_copy(update={"open_time": tuesday})
    )
    assert evaluate_condition(condition, {"005930": snapshot2}, {}) is False


def test_time_condition_naive_datetime_raises() -> None:
    condition = TimeCondition(at=time(9, 0))
    snapshot = MarketSnapshot(
        candle=make_candle(close=Decimal("100")).model_copy(
            update={"open_time": datetime(2026, 1, 5, 9, 0)}
        )
    )
    with pytest.raises(ValueError, match="naive"):
        evaluate_condition(condition, {"005930": snapshot}, {})


def test_evaluate_conditions_is_and_semantics() -> None:
    price = PriceCondition(symbol="005930", operator=">", threshold=Decimal("100"))
    indicator = IndicatorCondition(
        symbol="005930", indicator="rsi14", operator=">", threshold=Decimal("70")
    )
    snapshot_both = MarketSnapshot(
        candle=make_candle(close=Decimal("101")), indicators={"rsi14": Decimal("80")}
    )
    snapshot_only_price = MarketSnapshot(candle=make_candle(close=Decimal("101")), indicators={})

    assert evaluate_conditions((price, indicator), {"005930": snapshot_both}, {}) is True
    assert evaluate_conditions((price, indicator), {"005930": snapshot_only_price}, {}) is False
