"""L4_risk_and_safety_v1.0.md#9 R-11 — `rules/correlation.py` 단위 테스트."""
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
from src.core.risk.rules.correlation import correlation

_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
# threshold=0.7, aggregate_exposure_max_pct=30.0
_POLICY: RiskPolicy = load_risk_policy()


def _inputs(**stats_overrides: object) -> RiskInputs:
    stats_fields: dict[str, object] = dict(
        correlated_exposure_pct=Decimal("10.0"),
        max_correlation=0.5,
        missing_pairs=(),
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


def test_allow_when_correlation_below_threshold():
    result = correlation(_inputs(max_correlation=0.5), _POLICY)
    assert result.outcome == RiskOutcome.ALLOW


def test_allow_when_correlated_but_exposure_within_cap():
    result = correlation(
        _inputs(max_correlation=0.9, correlated_exposure_pct=Decimal("29.0")), _POLICY
    )
    assert result.outcome == RiskOutcome.ALLOW


def test_denies_when_correlated_and_exposure_exceeds_cap():
    result = correlation(
        _inputs(max_correlation=0.9, correlated_exposure_pct=Decimal("30.1")), _POLICY
    )
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_CORRELATION_EXPOSURE_EXCEEDED"


def test_denies_unconditionally_when_missing_pairs_present_even_if_metrics_look_safe():
    # 미지 페어를 0.0(무상관)으로 암묵 치환해 통과시키던 레거시 결함을 재현하지 않는다.
    result = correlation(
        _inputs(
            max_correlation=0.1,
            correlated_exposure_pct=Decimal("0.0"),
            missing_pairs=("BTC/USDT:ETH/USDT",),
        ),
        _POLICY,
    )
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("stats.missing_pairs",)


def test_denies_as_missing_when_correlated_exposure_pct_is_none():
    result = correlation(_inputs(correlated_exposure_pct=None), _POLICY)
    assert result.missing_fields == ("stats.correlated_exposure_pct",)


def test_denies_as_missing_when_max_correlation_is_none():
    result = correlation(_inputs(max_correlation=None), _POLICY)
    assert result.missing_fields == ("stats.max_correlation",)
