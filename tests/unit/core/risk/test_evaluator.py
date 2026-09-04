"""L4_risk_and_safety_v1.0.md#4.2, #9 R-16 — `evaluator.evaluate` 단위 테스트.

DoD(task-1193): 평가 순서 고정·첫 DENY 단락(통과 규칙은 끝까지 기록)·
심각도 합성(PAUSE>REDUCE>ESCALATE>ALLOW)·REDUCE 조건(수량 축소로 해소
가능·축소 후 0이면 DENY)·규칙 예외=DENY·latency_us>0.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

import src.core.risk.evaluator as evaluator_module
from src.core.risk.decision import GateKind, RiskOutcome
from src.core.risk.evaluator import _ORDER, evaluate
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, _order_intent, sample_inputs

_TTL_SEC = 5.0


def _bundle(**overrides: object) -> RiskRuleBundle:
    fields: dict[str, object] = dict(
        id=uuid4(),
        version="policy-v1",
        rule_hash="a" * 64,
        engine_version="engine-test-1",
        policy_snapshot=POLICY.model_dump(mode="python"),
        state=BundleState.ACTIVE,
        created_by=uuid4(),
        approved_by=uuid4(),
    )
    fields.update(overrides)
    return RiskRuleBundle(**fields)  # type: ignore[arg-type]


def _safe_inputs(**overrides: object):
    """모든 규칙이 ALLOW하는 기준선 — 개별 테스트가 특정 필드만 깨뜨린다."""
    base: dict[str, object] = dict(
        equity=EquityInputs(
            total_equity=Decimal("100000"),
            daily_pnl_pct=Decimal("0"),
            drawdown_pct=Decimal("0"),
            as_of=NOW,
        ),
        exposure=ExposureSnapshot(
            position_quantity=Decimal("0"),
            symbol_market_value=Decimal("0"),
            gross_leverage=Decimal("1"),
            as_of=NOW,
        ),
        stats=StatsInputs(
            var_pct=Decimal("1"),
            es_pct=Decimal("1"),
            var_method="parametric",
            bars_used=100,
            lookback_bars=100,
            correlated_exposure_pct=Decimal("1"),
            max_correlation=0.1,
            as_of=NOW,
        ),
        activity=ActivityInputs(trades_last_1h=1, trades_avg_per_hour_24h=Decimal("10")),
        safety=SafetyInputs(
            circuit_breaker_level="normal",
            active_control_scopes=(),
            data_distrust_level="TRUSTED",
            distrust_sources_available=3,
            connection_fresh=True,
            execution_paused_by_safety=False,
            rule_bundle_active=True,
        ),
    )
    base.update(overrides)
    return sample_inputs(**base)


def _evaluate(
    inputs,
    *,
    bundle=None,
    gate_kind=GateKind.PRE_TRADE,
    trace_id=None,
    now=NOW,
    ttl=_TTL_SEC,
):
    return evaluate(
        inputs,
        bundle or _bundle(),
        gate_kind=gate_kind,
        trace_id=trace_id or uuid4(),
        now=now,
        ttl=ttl,
    )


# ---- 평가 순서 고정 ----


def test_evaluation_order_matches_spec_4_2():
    assert [rule_id for rule_id, _ in _ORDER] == [
        "safety_state",
        "exposure_limits",
        "daily_loss",
        "max_drawdown",
        "leverage",
        "concentration",
        "strategy_allocation",
        "var_es",
        "correlation",
        "trade_frequency",
    ]


def test_all_rules_recorded_in_order_when_all_allow():
    decision = _evaluate(_safe_inputs())
    assert decision.outcome == RiskOutcome.ALLOW
    assert [r.rule_id for r in decision.rule_results] == [rule_id for rule_id, _ in _ORDER]


# ---- 첫 DENY 단락 ----


def test_first_deny_short_circuits_and_omits_later_rules():
    inputs = _safe_inputs(safety=SafetyInputs(circuit_breaker_level="halted"))
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.DENY
    assert [r.rule_id for r in decision.rule_results] == ["safety_state"]


def test_short_circuit_only_applies_to_deny_not_pause():
    """PAUSE(connection_fresh=False)는 단락하지 않는다 — 뒤 규칙도 전부 기록된다."""
    inputs = _safe_inputs(
        safety=SafetyInputs(
            circuit_breaker_level="normal",
            active_control_scopes=(),
            data_distrust_level="TRUSTED",
            execution_paused_by_safety=False,
            connection_fresh=False,
        )
    )
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.PAUSE
    assert [r.rule_id for r in decision.rule_results] == [rule_id for rule_id, _ in _ORDER]


# ---- 심각도 합성 ----


def test_pause_outperforms_escalate():
    inputs = _safe_inputs(
        equity=EquityInputs(
            total_equity=Decimal("100000"),
            daily_pnl_pct=Decimal("-4"),  # warning(3) < 4 < halt(5) → ESCALATE
            drawdown_pct=Decimal("0"),
            as_of=NOW,
        ),
        safety=SafetyInputs(
            circuit_breaker_level="normal",
            active_control_scopes=(),
            data_distrust_level="TRUSTED",
            execution_paused_by_safety=False,
            connection_fresh=False,  # PAUSE
        ),
    )
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.PAUSE


def test_escalate_when_no_deny_or_pause():
    inputs = _safe_inputs(
        equity=EquityInputs(
            total_equity=Decimal("100000"),
            daily_pnl_pct=Decimal("-4"),  # ESCALATE(RISK_DAILY_LOSS_WARN)
            drawdown_pct=Decimal("0"),
            as_of=NOW,
        )
    )
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.ESCALATE
    assert "RISK_DAILY_LOSS_WARN" in decision.reason_codes


def test_allow_when_every_rule_allows():
    decision = _evaluate(_safe_inputs())
    assert decision.outcome == RiskOutcome.ALLOW
    assert decision.reason_codes == ()
    assert decision.obligations == ()


# ---- REDUCE ----


def test_reduce_when_quantity_reduction_resolves_breach():
    intent = _order_intent(quantity=Decimal("1.000000"), notional=Decimal("5000"))
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("3000"),
        hard=True,
        limit_id=uuid4(),
    )
    inputs = _safe_inputs(intent=intent, limits=(limit,))
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.REDUCE
    assert decision.obligations == ("REDUCE_QUANTITY_TO:0.600000",)
    # 단락하지 않는다 — REDUCE로 흡수된 DENY 이후 규칙도 전부 평가된다.
    assert [r.rule_id for r in decision.rule_results] == [rule_id for rule_id, _ in _ORDER]


def test_reduce_requires_reduce_only_false():
    intent = _order_intent(
        quantity=Decimal("1.000000"), notional=Decimal("5000"), reduce_only=True
    )
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("3000"),
        hard=True,
        limit_id=uuid4(),
    )
    inputs = _safe_inputs(intent=intent, limits=(limit,))
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.DENY


def test_reduce_to_zero_denies_instead():
    intent = _order_intent(quantity=Decimal("1"), notional=Decimal("5000"))
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("1"),
        hard=True,
        limit_id=uuid4(),
    )
    inputs = _safe_inputs(intent=intent, limits=(limit,))
    decision = _evaluate(inputs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.obligations == ()


# ---- 규칙 예외 = DENY(승인으로 새지 않는다) ----


def test_rule_exception_denies_not_allows(monkeypatch):
    def _boom(_inputs, _policy):
        raise RuntimeError("boom")

    patched = tuple((rid, _boom if rid == "leverage" else fn) for rid, fn in _ORDER)
    monkeypatch.setattr(evaluator_module, "_ORDER", patched)

    decision = _evaluate(_safe_inputs())
    assert decision.outcome == RiskOutcome.DENY
    assert decision.rule_results[-1].rule_id == "leverage"
    assert decision.rule_results[-1].reason_code == "RISK_RULE_ERROR:leverage"
    assert [r.rule_id for r in decision.rule_results] == [
        "safety_state",
        "exposure_limits",
        "daily_loss",
        "max_drawdown",
        "leverage",
    ]


# ---- latency_us / 결정 계약 배선 ----


def test_latency_us_is_positive():
    decision = _evaluate(_safe_inputs())
    assert decision.latency_us > 0


def test_decision_carries_bundle_and_input_identity():
    bundle = _bundle(version="policy-v9", rule_hash="b" * 64, engine_version="engine-9")
    trace_id = uuid4()
    inputs = _safe_inputs()
    decision = _evaluate(inputs, bundle=bundle, trace_id=trace_id, gate_kind=GateKind.PRE_SUBMIT)
    assert decision.rule_version == "policy-v9"
    assert decision.rule_hash == "b" * 64
    assert decision.engine_version == "engine-9"
    assert decision.inputs_hash == inputs.inputs_hash()
    assert decision.subject_fingerprint == inputs.inputs_hash()
    assert decision.trace_id == trace_id
    assert decision.gate_kind == GateKind.PRE_SUBMIT
    assert decision.tenant_id == inputs.tenant_id
    assert decision.expires_at == NOW + timedelta(seconds=_TTL_SEC)


def test_decision_id_is_deterministic_for_same_inputs():
    inputs = _safe_inputs()
    trace_id = uuid4()
    bundle = _bundle()
    kwargs = dict(gate_kind=GateKind.PRE_TRADE, trace_id=trace_id, now=NOW, ttl=_TTL_SEC)
    first = evaluate(inputs, bundle, **kwargs)
    second = evaluate(inputs, bundle, **kwargs)
    assert first.decision_id == second.decision_id


def test_naive_now_is_rejected():
    with pytest.raises(ValidationError):
        _evaluate(_safe_inputs(), now=datetime(2026, 9, 3))
