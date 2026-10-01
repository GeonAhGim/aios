"""DEEPEN — negative / failure-injection / performance 보강 (task-10114).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2)) — negative test 0건이던
__init__.py를 DEEPEN 기준에 맞춰 보강한다.

DoD:
- negative test 3건 이상 (불변식 위반 입력을 명시적으로 거부)
- 실패주입 케이스 1건 이상 (monkeypatch로 의존성 예외 유발)
- `pytest tests/foundation/unit/automation/__init__.py -q` 통과
- docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.market_data import Candle
from src.foundation.automation.contracts.v1 import (
    ActionKind,
    AutomationRule,
    HedgeAction,
    NotifyAction,
    RuleStatus,
)
from src.foundation.automation.ports.gate import GateDecision
from src.foundation.risk_gate.contracts.v1 import RiskOutcome

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ── negative tests: rule schema violations ────────────────────────────


def test_rule_with_empty_conditions_raises() -> None:
    """Negative: AutomationRule must reject zero conditions (I-07: hard-fail
    condition must be computable and actually return failure)."""
    with pytest.raises(ValidationError, match="규칙에는 조건이 최소 1개 필요하다"):
        AutomationRule(
            rule_id=uuid4(),
            tenant_id=uuid4(),
            name="no-conditions",
            conditions=(),
            action=NotifyAction(message_template="test"),
            status=RuleStatus.ACTIVE,
            created_at=_EPOCH,
            updated_at=_EPOCH,
        )


def test_hedge_action_zero_quantity_raises() -> None:
    """Negative: HedgeAction quantity must be > 0 — zero or negative is
    rejected at the schema level."""
    with pytest.raises(ValueError, match="quantity는 0보다 커야 한다"):
        HedgeAction(
            kind=ActionKind.HEDGE,
            symbol="005930",
            hedge_symbol="005930",
            quantity=Decimal("0"),
        )


def test_hedge_action_negative_quantity_raises() -> None:
    """Negative: HedgeAction quantity must be > 0 — negative raises."""
    with pytest.raises(ValueError, match="quantity는 0보다 커야 한다"):
        HedgeAction(
            kind=ActionKind.HEDGE,
            symbol="005930",
            hedge_symbol="005930",
            quantity=Decimal("-1"),
        )


# ── negative tests: gate decision invariants ─────────────────────────


def test_gate_decision_allowed_requires_both_allow() -> None:
    """Negative: GateDecision.allowed must be False when risk is ALLOW but
    compliance is DENY (I-09: two independent authorities, fail-closed)."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.ALLOW,
        risk_decision_id=uuid4(),
        compliance_outcome=RiskOutcome.DENY,
        compliance_decision_id=uuid4(),
    )
    assert decision.allowed is False


def test_gate_decision_allowed_requires_both_decision_ids() -> None:
    """Negative: GateDecision.allowed must be False when either decision id
    is missing, even if both outcomes are ALLOW (fail-closed)."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.ALLOW,
        risk_decision_id=None,  # missing
        compliance_outcome=RiskOutcome.ALLOW,
        compliance_decision_id=uuid4(),
    )
    assert decision.allowed is False

    decision2 = GateDecision(
        risk_outcome=RiskOutcome.ALLOW,
        risk_decision_id=uuid4(),
        compliance_outcome=RiskOutcome.ALLOW,
        compliance_decision_id=None,  # missing
    )
    assert decision2.allowed is False


# ── failure-injection test ───────────────────────────────────────────


def test_evaluate_conditions_missing_indicator_propagates_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection: if the shared `compare_value` dependency raises
    unexpectedly (e.g. corrupted operator), the exception must propagate
    rather than silently returning False. This mirrors
    test_indicator_condition_compare_value_failure_propagates in
    test_evaluate.py but is a required negative for this module per DEEPEN.
    """
    from src.foundation.automation.contracts.v1 import IndicatorCondition
    from src.foundation.automation.domain.evaluate import (
        MarketSnapshot,
        evaluate_conditions,
    )

    def _boom(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("compare_value backend unavailable")

    # Patch at the module where it's imported
    import src.foundation.automation.domain.evaluate as evaluate_module

    monkeypatch.setattr(evaluate_module, "compare_value", _boom)

    condition = IndicatorCondition(
        symbol="005930", indicator="rsi14", operator=">", threshold=Decimal("70")
    )
    snapshot = MarketSnapshot(
        candle=Candle(
            symbol="005930",
            exchange="BITGET",
            timeframe="1m",
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("100"),
            open_time=_EPOCH,
            close_time=_EPOCH,
        ),
        indicators={"rsi14": Decimal("75")},
    )
    with pytest.raises(RuntimeError, match="compare_value backend unavailable"):
        evaluate_conditions((condition,), {"005930": snapshot}, {})


# ── performance assertion ────────────────────────────────────────────


@pytest.mark.perf
def test_evaluate_conditions_500_price_conditions_under_200ms() -> None:
    """Numeric performance assertion: 500 AND-composed price conditions
    against one snapshot must complete well under 200ms — pure in-memory
    comparison, no I/O."""
    import time as time_module

    from src.foundation.automation.contracts.v1 import PriceCondition, PriceField
    from src.foundation.automation.domain.evaluate import (
        MarketSnapshot,
        evaluate_conditions,
    )

    conditions = tuple(
        PriceCondition(
            symbol="005930",
            field=PriceField.CLOSE,
            operator=">",
            threshold=Decimal(str(i)),
        )
        for i in range(500)
    )
    snapshot = MarketSnapshot(
        candle=Candle(
            symbol="005930",
            exchange="BITGET",
            timeframe="1m",
            open=Decimal("1000"),
            high=Decimal("1000"),
            low=Decimal("1000"),
            close=Decimal("1000"),
            volume=Decimal("100"),
            open_time=_EPOCH,
            close_time=_EPOCH,
        )
    )
    started = time_module.perf_counter()
    result = evaluate_conditions(conditions, {"005930": snapshot}, {})
    elapsed = time_module.perf_counter() - started
    assert result is True
    assert elapsed < 0.2, f"evaluate_conditions took {elapsed:.3f}s, budget 0.2s"
