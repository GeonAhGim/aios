"""CM-15 exact normalization, fail-closed, and reproducibility tests."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.mandates.contracts.v1 import ComplianceDecision, ComplianceVerdict
from src.foundation.mandates.reporting.domain.trade_report import (
    TradeReportInputError,
    normalize_trade_report,
    to_domestic_report_fields,
)


@pytest.fixture
def compliance_decision() -> ComplianceDecision:
    return ComplianceDecision(
        decision_id=uuid4(),
        verdict=ComplianceVerdict.ALLOW,
        rule_hits=[],
        inputs_hash="a" * 64,
        bundle_version="b" * 64,
        evaluated_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )


@pytest.fixture
def inputs(compliance_decision: ComplianceDecision) -> dict[str, Any]:
    return dict(
        order_id=uuid4(),
        trade_id=uuid4(),
        compliance_decision=compliance_decision,
        instrument="005930",
        venue="KRX",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("70000"),
        currency="KRW",
        executed_at=datetime(2026, 9, 9, 1, 2, 3, tzinfo=timezone.utc),
        trader_id="trader-1",
    )


def test_normalizes_exact_fields(inputs: dict[str, Any]) -> None:
    record = normalize_trade_report(**inputs)
    assert record.order_id == inputs["order_id"]
    assert record.trade_id == inputs["trade_id"]
    assert record.compliance_decision_id == inputs["compliance_decision"].decision_id
    assert record.instrument == "005930"
    assert record.venue == "KRX"
    assert record.side == OrderSide.BUY
    assert record.quantity == Decimal("10")
    assert record.price == Decimal("70000")
    assert record.currency == "KRW"
    assert record.trader_id == "trader-1"
    assert len(record.report_hash) == 64
    int(record.report_hash, 16)  # hex


def test_reproduces_identical_hash_across_calls(inputs: dict[str, Any]) -> None:
    """Re-generating a report from the same inputs is byte-for-byte identical
    (CM-16's WORM write needs this before it will accept a regenerated row)."""
    first = normalize_trade_report(**inputs)
    second = normalize_trade_report(**inputs)
    assert first == second
    assert first.report_hash == second.report_hash


@pytest.mark.parametrize(
    "field,other",
    [
        ("order_id", uuid4()),
        ("trade_id", uuid4()),
        ("instrument", "000660"),
        ("venue", "NASDAQ"),
        ("side", OrderSide.SELL),
        ("quantity", Decimal("11")),
        ("price", Decimal("70001")),
        ("currency", "USD"),
        ("executed_at", datetime(2026, 9, 9, 1, 2, 4, tzinfo=timezone.utc)),
        ("trader_id", "trader-2"),
    ],
)
def test_hash_changes_when_any_field_changes(
    inputs: dict[str, Any],
    field: str,
    other: object,
) -> None:
    baseline = normalize_trade_report(**inputs)
    inputs[field] = other
    changed = normalize_trade_report(**inputs)
    assert changed.report_hash != baseline.report_hash


def test_hash_changes_with_different_compliance_decision(
    inputs: dict[str, Any],
    compliance_decision: ComplianceDecision,
) -> None:
    baseline = normalize_trade_report(**inputs)
    other_decision = ComplianceDecision(
        decision_id=uuid4(),
        verdict=compliance_decision.verdict,
        rule_hits=[],
        inputs_hash=compliance_decision.inputs_hash,
        bundle_version=compliance_decision.bundle_version,
        evaluated_at=compliance_decision.evaluated_at,
    )
    inputs["compliance_decision"] = other_decision
    changed = normalize_trade_report(**inputs)
    assert changed.report_hash != baseline.report_hash
    assert changed.compliance_decision_id == other_decision.decision_id


def test_deny_decision_still_normalizes(inputs: dict[str, Any]) -> None:
    """A DENY verdict must still produce a report — post-trade audit needs
    to see what should have been blocked, not silence."""
    inputs["compliance_decision"] = ComplianceDecision(
        decision_id=uuid4(),
        verdict=ComplianceVerdict.DENY,
        rule_hits=[],
        inputs_hash="c" * 64,
        bundle_version="d" * 64,
        evaluated_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    record = normalize_trade_report(**inputs)
    assert record.compliance_decision_id == inputs["compliance_decision"].decision_id


@pytest.mark.parametrize("value", [None, "not-a-decision", 42])
def test_missing_compliance_decision_rejected(inputs: dict[str, Any], value: object) -> None:
    inputs["compliance_decision"] = value
    with pytest.raises(TradeReportInputError, match="compliance_decision is required"):
        normalize_trade_report(**inputs)


def test_invalid_side_rejected(inputs: dict[str, Any]) -> None:
    inputs["side"] = "BUY"
    with pytest.raises(TradeReportInputError, match="side must be an OrderSide"):
        normalize_trade_report(**inputs)


@pytest.mark.parametrize("field", ["quantity", "price"])
@pytest.mark.parametrize(
    "value", [100.5, Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")]
)
def test_invalid_numbers_rejected(inputs: dict[str, Any], field: str, value: object) -> None:
    inputs[field] = value
    with pytest.raises(TradeReportInputError, match="finite positive Decimal"):
        normalize_trade_report(**inputs)


@pytest.mark.parametrize("field", ["instrument", "venue", "currency", "trader_id"])
@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_strings_rejected(inputs: dict[str, Any], field: str, value: object) -> None:
    inputs[field] = value
    with pytest.raises(TradeReportInputError, match="non-blank string"):
        normalize_trade_report(**inputs)


def test_naive_datetime_rejected(inputs: dict[str, Any]) -> None:
    inputs["executed_at"] = datetime(2026, 9, 9, 1, 2, 3)
    with pytest.raises(TradeReportInputError, match="tz-aware"):
        normalize_trade_report(**inputs)


def test_domestic_report_fields_exact_mapping(inputs: dict[str, Any]) -> None:
    record = normalize_trade_report(**inputs)
    fields = to_domestic_report_fields(record)
    assert fields == {
        "종목코드": "005930",
        "거래소": "KRX",
        "매매구분": "매수",
        "수량": "10",
        "단가": "70000",
        "통화": "KRW",
        "체결시각": record.executed_at.isoformat(),
        "주문번호": str(record.order_id),
        "체결번호": str(record.trade_id),
        "판정ID": str(record.compliance_decision_id),
        "보고서해시": record.report_hash,
    }


def test_domestic_report_fields_sell_side_label(inputs: dict[str, Any]) -> None:
    inputs["side"] = OrderSide.SELL
    record = normalize_trade_report(**inputs)
    assert to_domestic_report_fields(record)["매매구분"] == "매도"


def test_domestic_report_fields_values_are_strings(inputs: dict[str, Any]) -> None:
    """The domestic layout is a submission-shaped string map, not a passthrough
    of typed record fields (Decimal/UUID/datetime would not serialize as-is)."""
    record = normalize_trade_report(**inputs)
    fields = to_domestic_report_fields(record)
    assert all(isinstance(value, str) for value in fields.values())
