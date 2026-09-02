"""L4_risk_and_safety_v1.0.md#3.1, #8, #9 R-02 — RiskDecision 계약 스냅샷.

`fixtures/risk_decision_v1.json`은 현재 스키마의 스냅샷이다. 향후 누군가
`RiskDecision`에서 필드를 지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다
(§8 "필드 제거 시 실패"). 필드를 의도적으로 추가하는 것은 107번 MINOR
변경이므로 허용되고, 그 경우에만 fixture를 함께 갱신한다.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome, RuleResult

FIXTURE = Path(__file__).parent / "fixtures" / "risk_decision_v1.json"


def _sample_decision(**overrides: object) -> RiskDecision:
    now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    base: dict[str, object] = dict(
        decision_id=uuid4(),
        gate_kind=GateKind.PRE_TRADE,
        tenant_id=uuid4(),
        execution_ref="exec:1",
        subject_fingerprint="a" * 64,
        outcome=RiskOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        rule_results=(),
        rule_version="2026.09.1",
        rule_hash="b" * 64,
        engine_version="risk-engine/2",
        inputs_hash="c" * 64,
        input_refs=(),
        evaluated_at=now,
        expires_at=now,
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=100,
    )
    base.update(overrides)
    return RiskDecision(**base)  # type: ignore[arg-type]


def test_schema_snapshot_matches_fixture():
    current = RiskDecision.model_json_schema()
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_naive_evaluated_at_rejected():
    with pytest.raises(ValidationError):
        _sample_decision(evaluated_at=datetime(2026, 9, 3, 0, 0))


def test_naive_expires_at_rejected():
    with pytest.raises(ValidationError):
        _sample_decision(expires_at=datetime(2026, 9, 3, 0, 0))


def test_is_actionable_true_for_allow_before_expiry():
    now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 3, 0, 0, 1, tzinfo=timezone.utc)
    decision = _sample_decision(evaluated_at=now, expires_at=later)
    assert decision.is_actionable(now) is True


def test_is_actionable_false_after_expiry():
    now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    past = datetime(2026, 9, 2, 23, 59, 59, tzinfo=timezone.utc)
    decision = _sample_decision(evaluated_at=past, expires_at=past)
    assert decision.is_actionable(now) is False


def test_is_actionable_false_for_deny():
    now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 3, 0, 0, 1, tzinfo=timezone.utc)
    decision = _sample_decision(outcome=RiskOutcome.DENY, evaluated_at=now, expires_at=later)
    assert decision.is_actionable(now) is False


def test_rule_result_missing_fields_without_deny_rejected():
    with pytest.raises(ValidationError):
        RuleResult(
            rule_id="daily_loss",
            outcome=RiskOutcome.ALLOW,
            unit="pct",
            missing_fields=("daily_pnl_pct",),
        )


def test_rule_result_missing_fields_with_deny_allowed():
    result = RuleResult(
        rule_id="daily_loss",
        outcome=RiskOutcome.DENY,
        unit="pct",
        missing_fields=("daily_pnl_pct",),
    )
    assert result.missing_fields == ("daily_pnl_pct",)


def test_rule_result_frozen():
    result = RuleResult(rule_id="daily_loss", outcome=RiskOutcome.ALLOW, unit="pct")
    with pytest.raises(ValidationError):
        result.outcome = RiskOutcome.DENY  # type: ignore[misc]


def test_decision_id_and_trace_id_are_uuid():
    decision = _sample_decision()
    assert str(decision.decision_id)
    assert str(decision.trace_id)


def test_observed_and_limit_decimal_precision_preserved():
    result = RuleResult(
        rule_id="var",
        outcome=RiskOutcome.DENY,
        observed=Decimal("5.123456"),
        limit=Decimal("5.0"),
        unit="pct",
    )
    assert result.observed == Decimal("5.123456")
