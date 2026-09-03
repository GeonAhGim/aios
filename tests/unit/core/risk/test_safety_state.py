"""L4_risk_and_safety_v1.0.md#9 R-13 — `rules/safety_state.py` 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.core.loader.risk_policy_loader import RiskPolicy, load_risk_policy
from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.rules.safety_state import safety_state

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
_POLICY: RiskPolicy = load_risk_policy()

_SAFE_STATE = dict(
    circuit_breaker_level="normal",
    active_control_scopes=(),
    data_distrust_level="NORMAL",
    execution_paused_by_safety=False,
    connection_fresh=True,
)


def _inputs(*, reduce_only: bool = False, **safety_overrides: object) -> RiskInputs:
    safety_fields: dict[str, object] = dict(_SAFE_STATE)
    safety_fields.update(safety_overrides)
    return RiskInputs(
        tenant_id=uuid4(),
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol="BTC/USDT",
            asset_class="CRYPTO_SPOT",
            side="BUY",
            quantity=Decimal("0.1"),
            ref_price=Decimal("50000"),
            notional=Decimal("5000"),
            reduce_only=reduce_only,
            strategy_id="strat-1",
            strategy_version="1.0",
            capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(as_of=_NOW),
        exposure=ExposureSnapshot(as_of=_NOW),
        stats=StatsInputs(as_of=_NOW),
        activity=ActivityInputs(),
        safety=SafetyInputs(**safety_fields),  # type: ignore[arg-type]
        limits=(),
        as_of=_NOW,
    )


def test_allow_when_all_five_inputs_are_safe():
    result = safety_state(_inputs(), _POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_denies_on_restricted_circuit_breaker_level():
    result = safety_state(_inputs(circuit_breaker_level="restricted"), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_CIRCUIT_BREAKER_RESTRICTED"


def test_escalates_on_warning_circuit_breaker_level():
    result = safety_state(_inputs(circuit_breaker_level="warning"), _POLICY)
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_CIRCUIT_BREAKER_WARNING"


def test_denies_on_active_control_scope():
    result = safety_state(_inputs(active_control_scopes=("TENANT:abc",)), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_KILL_SWITCH_ACTIVE_TENANT:abc"


def test_denies_on_distrusted_data_level_for_new_entry():
    result = safety_state(_inputs(data_distrust_level="DISTRUSTED"), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_DATA_DISTRUST_DISTRUSTED"


def test_allows_distrusted_data_level_when_reduce_only():
    result = safety_state(
        _inputs(data_distrust_level="DISTRUSTED", reduce_only=True), _POLICY
    )
    assert result.outcome == RiskOutcome.ALLOW


def test_denies_when_execution_paused_by_safety():
    result = safety_state(_inputs(execution_paused_by_safety=True), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_EXECUTION_PAUSED_BY_SAFETY"


def test_pauses_when_connection_not_fresh():
    result = safety_state(_inputs(connection_fresh=False), _POLICY)
    assert result.outcome == RiskOutcome.PAUSE
    assert result.reason_code == "RISK_INPUT_STALE"


def test_denies_as_missing_when_circuit_breaker_level_is_none():
    result = safety_state(_inputs(circuit_breaker_level=None), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("safety.circuit_breaker_level",)


def test_denies_as_missing_when_active_control_scopes_is_none():
    result = safety_state(_inputs(active_control_scopes=None), _POLICY)
    assert result.missing_fields == ("safety.active_control_scopes",)


def test_denies_as_missing_when_data_distrust_level_is_none():
    result = safety_state(_inputs(data_distrust_level=None), _POLICY)
    assert result.missing_fields == ("safety.data_distrust_level",)


def test_denies_as_missing_when_execution_paused_by_safety_is_none():
    result = safety_state(_inputs(execution_paused_by_safety=None), _POLICY)
    assert result.missing_fields == ("safety.execution_paused_by_safety",)


def test_denies_as_missing_when_connection_fresh_is_none():
    result = safety_state(_inputs(connection_fresh=None), _POLICY)
    assert result.missing_fields == ("safety.connection_fresh",)
