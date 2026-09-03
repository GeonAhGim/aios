"""L4_risk_and_safety_v1.0.md#9 R-12 — `rules/trade_frequency.py` 단위 테스트."""
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
from src.core.risk.rules.trade_frequency import trade_frequency

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
# anomaly_multiplier=3.0, max_trades_per_hour=60
_POLICY: RiskPolicy = load_risk_policy()


def _inputs(**activity_overrides: object) -> RiskInputs:
    activity_fields: dict[str, object] = dict(
        trades_last_1h=1, trades_avg_per_hour_24h=Decimal("2")
    )
    activity_fields.update(activity_overrides)
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
            reduce_only=False,
            strategy_id="strat-1",
            strategy_version="1.0",
            capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(as_of=_NOW),
        exposure=ExposureSnapshot(as_of=_NOW),
        stats=StatsInputs(as_of=_NOW),
        activity=ActivityInputs(**activity_fields),  # type: ignore[arg-type]
        safety=SafetyInputs(),
        limits=(),
        as_of=_NOW,
    )


def test_allow_within_normal_frequency():
    result = trade_frequency(_inputs(), _POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_allows_exactly_at_anomaly_multiplier_boundary():
    # avg*3.0 = 90 > max_trades_per_hour(60) — 배수 상한이 지배하는 경계.
    result = trade_frequency(
        _inputs(trades_last_1h=90, trades_avg_per_hour_24h=Decimal("30")), _POLICY
    )
    assert result.outcome == RiskOutcome.ALLOW


def test_denies_just_over_anomaly_multiplier_boundary():
    result = trade_frequency(
        _inputs(trades_last_1h=91, trades_avg_per_hour_24h=Decimal("30")), _POLICY
    )
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_TRADE_FREQUENCY_ANOMALY"


def test_allows_exactly_at_absolute_cap_boundary():
    # avg*3.0 = 15 < max_trades_per_hour(60) — 절대 상한이 지배하는 경계.
    result = trade_frequency(
        _inputs(trades_last_1h=60, trades_avg_per_hour_24h=Decimal("5")), _POLICY
    )
    assert result.outcome == RiskOutcome.ALLOW


def test_denies_just_over_absolute_cap_boundary():
    result = trade_frequency(
        _inputs(trades_last_1h=61, trades_avg_per_hour_24h=Decimal("5")), _POLICY
    )
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_TRADE_FREQUENCY_ANOMALY"


def test_denies_as_missing_when_trades_last_1h_is_none():
    result = trade_frequency(_inputs(trades_last_1h=None), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("activity.trades_last_1h",)


def test_denies_as_missing_when_trades_avg_per_hour_24h_is_none():
    result = trade_frequency(_inputs(trades_avg_per_hour_24h=None), _POLICY)
    assert result.missing_fields == ("activity.trades_avg_per_hour_24h",)
