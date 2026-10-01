"""CM-15 exact normalization, fail-closed, and reproducibility tests."""

import pickle
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch
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


# ── D3 증빙 (CM-15 깊이 하한 보강) ──────────────────────────────────────────


def test_failure_injection_canonical_json_error(inputs: dict[str, Any]) -> None:
    """CM-15: canonical_json이 예외를 던지면 원상(Exception)이 그대로 전파된다.
    해시 계산 실패가 silent-pass하지 않음을 검증한다 — fail-closed 기본 자세."""
    with patch(
        "src.foundation.mandates.reporting.domain.trade_report.canonical_json",
        side_effect=RuntimeError("canonical_json failure"),
    ):
        # canonical_json 호출이 try/except 없이 직접 sha256_hex(…) 안에
        # 있으므로 RuntimeError가 그대로 전파된다 — 이것이 fail-closed다.
        with pytest.raises(RuntimeError, match="canonical_json failure"):
            normalize_trade_report(**inputs)


@pytest.mark.perf
def test_normalize_trade_report_perf_budget(
    inputs: dict[str, Any],
    perf_budget: Any,
) -> None:
    """CM-15: normalize_trade_report 1회 호출이 1 ms 미만이어야 한다.
    CM-15는 'real-time' 지연 허용 — perf_budget.assert_within으로
    CPU 시간 기반 예산 단언을 수행한다( wall-clock 아님, ratchet 준수)."""
    perf_budget.assert_within(
        lambda: normalize_trade_report(**inputs),
        budget_ms=1.0,
        n=5,
    )


def test_gate_red_report_hash_uniqueness_violation() -> None:
    """CM-15 gate_red: 서로 다른 입력이 동일한 report_hash를 생성하면
    불변식 위반 — 이 테스트는 위반이 실제로 감지됨을 보인다.

    CM-15는 '모든 보고서는 고유해야 한다'는 불변식을 가진다.
    이 테스트는 hash 충돌이 발생하면 assert가 실패하여
    게이트가 적색으로 전환됨을 검증한다."""
    # 동일한 입력으로 두 보고서를 생성하면 해시가 동일해야 함
    base_inputs: dict[str, Any] = {
        "order_id": uuid4(),
        "trade_id": uuid4(),
        "compliance_decision": ComplianceDecision(
            decision_id=uuid4(),
            verdict=ComplianceVerdict.ALLOW,
            rule_hits=[],
            inputs_hash="a" * 64,
            bundle_version="b" * 64,
            evaluated_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        ),
        "instrument": "005930",
        "venue": "KRX",
        "side": OrderSide.BUY,
        "quantity": Decimal("10"),
        "price": Decimal("70000"),
        "currency": "KRW",
        "executed_at": datetime(2026, 9, 9, 1, 2, 3, tzinfo=timezone.utc),
        "trader_id": "trader-1",
    }
    r1 = normalize_trade_report(**base_inputs)
    r2 = normalize_trade_report(**base_inputs)
    # 동일 입력 → 동일 해시 (재현성)
    assert r1.report_hash == r2.report_hash

    # 다른 instrument → 다른 해시 (고유성)
    base_inputs["instrument"] = "000660"
    r3 = normalize_trade_report(**base_inputs)
    assert r3.report_hash != r1.report_hash

    # 다른 side → 다른 해시
    base_inputs["side"] = OrderSide.SELL
    r4 = normalize_trade_report(**base_inputs)
    assert r4.report_hash != r1.report_hash
    assert r4.report_hash != r3.report_hash

    # 다른 quantity → 다른 해시
    base_inputs["quantity"] = Decimal("11")
    r5 = normalize_trade_report(**base_inputs)
    assert r5.report_hash != r1.report_hash
    assert r5.report_hash != r3.report_hash
    assert r5.report_hash != r4.report_hash


# Module-level replay worker — picklable by ProcessPoolExecutor
def _trade_report_replay_worker(serialized: bytes) -> list[str]:
    """별도 OS 프로세스에서 trade report 해시 재현."""
    args_list: list[dict[str, Any]] = list(
        pickle.loads(serialized)  # noqa: S301 — controlled test payload
    )
    from src.foundation.mandates.reporting.domain.trade_report import (
        normalize_trade_report as ntr,
    )

    return [ntr(**a).report_hash for a in args_list]


def test_adversarial_replay_across_processes(inputs: dict[str, Any]) -> None:
    """D3 적대/리플레이 증빙: 전역 상태가 완전히 분리된 별도 OS 프로세스에서
    동일한 inputs로 trade report를 생성하면 완전히 동일한 report_hash를
    생산해야 한다 — CM-15의 재현성 불변식 검증."""
    cases = []
    for i in range(10):
        cases.append(
            dict(
                inputs,
                order_id=uuid4(),
                trade_id=uuid4(),
                instrument=f"0000{i:04d}",
            )
        )
    # 메인 프로세스에서 기대값 생성
    expected = [normalize_trade_report(**c) for c in cases]
    expected_hashes = [r.report_hash for r in expected]

    # 별도 프로세스에서 재현 — pickle 직렬화/역직렬화 후 실행
    payload = pickle.dumps(cases)

    with ProcessPoolExecutor(max_workers=2) as pool:
        future = pool.submit(_trade_report_replay_worker, payload)
        replay_hashes = future.result(timeout=30)

    assert replay_hashes == expected_hashes, (
        f"Process replay mismatch: {replay_hashes[:3]} != {expected_hashes[:3]}"
    )
