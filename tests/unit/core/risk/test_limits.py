"""L4_risk_and_safety_v1.0.md#2.1, §9 R-14 — `check_exposure_limits` 판정 테스트.

DoD(task-1186): 6개 scope(TENANT/ACCOUNT/STRATEGY/SYMBOL/ASSET_CLASS/
PROVIDER) 각각 매칭·비매칭(특히 SYMBOL 한도가 다른 심볼에 미적용),
hard=DENY·soft=ESCALATE, 한도 경계값(=통과, 초과=거부), 입력 결손
fail-closed DENY.
"""
from decimal import Decimal
from uuid import uuid4

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import ActivityInputs, ExposureSnapshot
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope, check_exposure_limits
from tests.unit.core.risk._rule_test_helpers import NOW, sample_inputs

_LOW_LIMIT = Decimal("1")  # sample_inputs()의 intent.notional(5000)보다 항상 작다


def _order_limit(
    *, scope: LimitScope, scope_ref: str, hard: bool = True, value: Decimal = _LOW_LIMIT
) -> ExposureLimit:
    return ExposureLimit(
        scope=scope,
        scope_ref=scope_ref,
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=value,
        hard=hard,
        limit_id=uuid4(),
    )


def _run(inputs, *limits):
    return check_exposure_limits(inputs, tuple(limits))


# ---- scope 매칭 (6종 각각 매칭/비매칭) ----


def test_tenant_scope_matches_and_denies():
    tenant_id = uuid4()
    inputs = sample_inputs(tenant_id=tenant_id)
    result = _run(inputs, _order_limit(scope=LimitScope.TENANT, scope_ref=str(tenant_id)))
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_LIMIT_BREACH:TENANT:MAX_ORDER_NOTIONAL"


def test_tenant_scope_other_tenant_not_applied():
    inputs = sample_inputs(tenant_id=uuid4())
    result = _run(inputs, _order_limit(scope=LimitScope.TENANT, scope_ref=str(uuid4())))
    assert result.outcome == RiskOutcome.ALLOW


def test_account_scope_matches_and_denies():
    tenant_id = uuid4()
    inputs = sample_inputs(tenant_id=tenant_id)
    result = _run(inputs, _order_limit(scope=LimitScope.ACCOUNT, scope_ref=str(tenant_id)))
    assert result.outcome == RiskOutcome.DENY


def test_account_scope_other_account_not_applied():
    inputs = sample_inputs(tenant_id=uuid4())
    result = _run(inputs, _order_limit(scope=LimitScope.ACCOUNT, scope_ref=str(uuid4())))
    assert result.outcome == RiskOutcome.ALLOW


def test_strategy_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-1"))
    assert result.outcome == RiskOutcome.DENY


def test_strategy_scope_other_strategy_not_applied():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-2"))
    assert result.outcome == RiskOutcome.ALLOW


def test_symbol_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT"))
    assert result.outcome == RiskOutcome.DENY


def test_symbol_scope_other_symbol_not_applied():
    """SYMBOL 한도가 다른 심볼 주문에 적용되지 않는다 — task-1186 DoD 핵심 케이스."""
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="ETH/USDT"))
    assert result.outcome == RiskOutcome.ALLOW


def test_asset_class_scope_matches_and_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.ASSET_CLASS, scope_ref="CRYPTO_SPOT"))
    assert result.outcome == RiskOutcome.DENY


def test_asset_class_scope_other_class_not_applied():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.ASSET_CLASS, scope_ref="EQUITY"))
    assert result.outcome == RiskOutcome.ALLOW


def test_provider_scope_matches_and_denies():
    inputs = sample_inputs(execution_ref="exec:1")
    result = _run(inputs, _order_limit(scope=LimitScope.PROVIDER, scope_ref="exec:1"))
    assert result.outcome == RiskOutcome.DENY


def test_provider_scope_other_provider_not_applied():
    inputs = sample_inputs(execution_ref="exec:1")
    result = _run(inputs, _order_limit(scope=LimitScope.PROVIDER, scope_ref="exec:2"))
    assert result.outcome == RiskOutcome.ALLOW


# ---- hard/soft ----


def test_hard_breach_denies():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=True))
    assert result.outcome == RiskOutcome.DENY


def test_soft_breach_escalates():
    inputs = sample_inputs()
    result = _run(inputs, _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=False))
    assert result.outcome == RiskOutcome.ESCALATE
    assert result.reason_code == "RISK_LIMIT_BREACH:SYMBOL:MAX_ORDER_NOTIONAL"


def test_hard_breach_overrides_prior_soft_escalate():
    inputs = sample_inputs()
    soft = _order_limit(scope=LimitScope.SYMBOL, scope_ref="BTC/USDT", hard=False)
    hard = _order_limit(scope=LimitScope.STRATEGY, scope_ref="strat-1", hard=True)
    result = _run(inputs, soft, hard)
    assert result.outcome == RiskOutcome.DENY


# ---- 경계값 ----


def test_limit_value_equal_to_observed_allows():
    inputs = sample_inputs()  # intent.notional == 5000
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("5000"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.ALLOW
    assert result.observed == Decimal("5000")


def test_limit_value_below_observed_denies():
    inputs = sample_inputs()
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=Decimal("4999.999999"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY


# ---- 입력 결손 fail-closed(I2) ----


def test_missing_gross_leverage_denies():
    inputs = sample_inputs()  # exposure.gross_leverage 기본값 None
    limit = ExposureLimit(
        scope=LimitScope.TENANT,
        scope_ref=str(inputs.tenant_id),
        metric=LimitMetric.MAX_LEVERAGE,
        limit_value=Decimal("3"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_INPUT_MISSING:exposure.gross_leverage"
    assert result.missing_fields == ("exposure.gross_leverage",)


def test_missing_trades_last_1h_denies():
    inputs = sample_inputs(activity=ActivityInputs(trades_last_1h=None))
    limit = ExposureLimit(
        scope=LimitScope.STRATEGY,
        scope_ref="strat-1",
        metric=LimitMetric.MAX_TRADES_PER_HOUR,
        limit_value=Decimal("10"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("activity.trades_last_1h",)


def test_missing_gross_notional_pct_key_denies():
    inputs = sample_inputs()  # exposure.gross_notional 기본값 {}
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("exposure.gross_notional[SYMBOL:BTC/USDT]",)


# ---- 기타 ----


def test_no_limits_allows():
    inputs = sample_inputs()
    result = _run(inputs)
    assert result.outcome == RiskOutcome.ALLOW


def test_gross_notional_pct_breach_denies():
    inputs = sample_inputs(
        exposure=ExposureSnapshot(gross_notional={"SYMBOL:BTC/USDT": Decimal("25")}, as_of=NOW)
    )
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    result = _run(inputs, limit)
    assert result.outcome == RiskOutcome.DENY
    assert result.observed == Decimal("25.000000")
