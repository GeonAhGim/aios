"""L4_risk_and_safety_v1.0.md#9 R-11 — `rules/var_es.py` 단위 테스트."""
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
from src.core.risk.rules.var_es import var_es

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
_POLICY: RiskPolicy = load_risk_policy()  # var.max_pct=5.0, es_max_pct=7.0, min_bars=60


def _inputs(**stats_overrides: object) -> RiskInputs:
    stats_fields: dict[str, object] = dict(
        var_pct=Decimal("2.5"),
        es_pct=Decimal("4.0"),
        var_method="cornish_fisher",
        bars_used=250,
        as_of=_NOW,
    )
    stats_fields.update(stats_overrides)
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
        stats=StatsInputs(**stats_fields),  # type: ignore[arg-type]
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=_NOW,
    )


def test_allow_when_var_and_es_within_limits():
    result = var_es(_inputs(), _POLICY)
    assert result.outcome == RiskOutcome.ALLOW
    assert result.observed == Decimal("2.500000")
    assert result.limit == Decimal("5.000000")


def test_denies_when_var_exceeds_max_pct():
    result = var_es(_inputs(var_pct=Decimal("5.1")), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_VAR_EXCEEDED"


def test_denies_when_es_exceeds_es_max_pct_even_if_var_ok():
    result = var_es(_inputs(es_pct=Decimal("7.1")), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_ES_EXCEEDED"


def test_denies_as_missing_when_var_pct_is_none():
    result = var_es(_inputs(var_pct=None), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("stats.var_pct",)
    assert result.reason_code == "RISK_INPUT_MISSING:stats.var_pct"


def test_denies_as_missing_when_es_pct_is_none():
    result = var_es(_inputs(es_pct=None), _POLICY)
    assert result.missing_fields == ("stats.es_pct",)


def test_denies_as_missing_when_var_method_is_none():
    result = var_es(_inputs(var_method=None), _POLICY)
    assert result.missing_fields == ("stats.var_method",)


def test_denies_as_missing_when_bars_used_is_none():
    result = var_es(_inputs(bars_used=None), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("stats.bars_used",)


def test_denies_as_missing_when_bars_used_below_min_bars():
    # 계산은 됐지만 표본이 60(min_bars) 미달 — 결손과 동일하게 취급(R3).
    result = var_es(_inputs(bars_used=59), _POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("stats.bars_used",)


def test_allows_at_exact_min_bars_boundary():
    result = var_es(_inputs(bars_used=60), _POLICY)
    assert result.outcome == RiskOutcome.ALLOW
