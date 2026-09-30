from __future__ import annotations

import time as time_module
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
from src.foundation.automation.domain import evaluate as evaluate_module
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


def test_price_condition_missing_symbol_is_fail_closed() -> None:
    """Negative: a symbol absent from `snapshots` must not be treated as
    "no data means always true" -- evaluate_condition stays fail-closed."""
    condition = PriceCondition(symbol="005930", operator=">", threshold=Decimal("100"))
    snapshot = MarketSnapshot(candle=make_candle(symbol="000660", close=Decimal("101")))
    assert evaluate_condition(condition, {"000660": snapshot}, {}) is False


def test_disclosure_condition_missing_symbol_is_fail_closed() -> None:
    """Negative: same fail-closed rule for DisclosureCondition when the rule's
    symbol has no snapshot at all (not merely an empty disclosure set)."""
    condition = DisclosureCondition(symbol="005930", filing_type="EARNINGS")
    assert evaluate_condition(condition, {}, {}) is False


def test_time_condition_no_snapshots_is_fail_closed() -> None:
    """Negative: TimeCondition has no symbol of its own -- an empty
    `snapshots` mapping must not crash or default to "triggered"."""
    condition = TimeCondition(at=time(9, 0))
    assert evaluate_condition(condition, {}, {}) is False


def test_automation_rule_rejects_empty_conditions() -> None:
    """Negative: `AutomationRule` must reject a rule with zero conditions at
    construction time rather than letting `evaluate_conditions` silently
    vacuously-True it later (`all(())` is True)."""
    from uuid import uuid4

    import pydantic

    from src.foundation.automation.contracts.v1 import (
        ActionKind,
        AutomationRule,
        NotifyAction,
        RuleStatus,
    )

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(pydantic.ValidationError, match="최소 1개 필요"):
        AutomationRule(
            rule_id=uuid4(),
            tenant_id=uuid4(),
            name="empty-conditions",
            conditions=(),
            action=NotifyAction(kind=ActionKind.NOTIFY, message_template="x"),
            status=RuleStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )


def test_evaluate_condition_unhandled_kind_raises() -> None:
    """Failure-injection: an object that satisfies none of the known
    `Condition` branches must not be silently treated as "not triggered" --
    it has to raise loudly (fail-closed on unknown input, not fail-open)."""

    class _NotACondition:
        symbol = "005930"

    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("100")))
    with pytest.raises(AssertionError, match="unhandled condition kind"):
        evaluate_condition(_NotACondition(), {"005930": snapshot}, {})  # type: ignore[arg-type]


def test_indicator_condition_compare_value_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection: if the shared `compare_value` dependency raises
    (e.g. a corrupted operator slipping past validation), evaluate_condition
    must let that exception propagate rather than swallowing it into a
    falsely "not triggered" result."""

    def _boom(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("compare_value backend unavailable")

    monkeypatch.setattr(evaluate_module, "compare_value", _boom)
    condition = IndicatorCondition(
        symbol="005930", indicator="rsi14", operator=">", threshold=Decimal("70")
    )
    snapshot = MarketSnapshot(
        candle=make_candle(close=Decimal("100")), indicators={"rsi14": Decimal("75")}
    )
    with pytest.raises(RuntimeError, match="compare_value backend unavailable"):
        evaluate_condition(condition, {"005930": snapshot}, {})


def test_evaluate_conditions_performance_budget() -> None:
    """Numeric performance assertion: evaluating 500 AND-composed price
    conditions against one snapshot must stay well under a loose 200ms
    budget -- this is a pure in-memory comparison loop, no I/O."""
    conditions = tuple(
        PriceCondition(symbol="005930", operator=">", threshold=Decimal(str(i))) for i in range(500)
    )
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("1000")))
    started = time_module.perf_counter()
    result = evaluate_conditions(conditions, {"005930": snapshot}, {})
    elapsed = time_module.perf_counter() - started
    assert result is True
    assert elapsed < 0.2
