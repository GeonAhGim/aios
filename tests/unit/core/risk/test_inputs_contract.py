"""L4_risk_and_safety_v1.0.md#3.2, #9 R-03 — RiskInputs 계약 테스트."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope


class _Allocation:
    def __init__(
        self, symbol: str, strategy_id: str, approved_quantity: Decimal, capital_pct: Decimal
    ):
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.approved_quantity = approved_quantity
        self.capital_pct = capital_pct


def _sample_inputs(**overrides: object) -> RiskInputs:
    now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    base: dict[str, object] = dict(
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
        equity=EquityInputs(
            total_equity=Decimal("10000"), daily_pnl_pct=Decimal("-1.5"), as_of=now
        ),
        exposure=ExposureSnapshot(position_quantity=Decimal("0"), as_of=now),
        stats=StatsInputs(var_pct=Decimal("2.5"), as_of=now),
        activity=ActivityInputs(trades_last_1h=1, trades_avg_per_hour_24h=Decimal("2")),
        safety=SafetyInputs(circuit_breaker_level="normal"),
        limits=(),
        as_of=now,
    )
    base.update(overrides)
    return RiskInputs(**base)  # type: ignore[arg-type]


def test_naive_as_of_rejected():
    with pytest.raises(ValidationError):
        _sample_inputs(as_of=datetime(2026, 9, 3, 0, 0))


def test_pct_quantized_to_six_decimals():
    as_of = datetime(2026, 9, 3, tzinfo=timezone.utc)
    inputs = _sample_inputs(
        equity=EquityInputs(daily_pnl_pct=Decimal("-1.123456789"), as_of=as_of)
    )
    assert inputs.equity.daily_pnl_pct == Decimal("-1.123457")


def test_inputs_hash_is_stable_across_calls():
    inputs = _sample_inputs()
    assert inputs.inputs_hash() == inputs.inputs_hash()


def test_inputs_hash_normalizes_decimal_trailing_zeros():
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    a = _sample_inputs(allocated_capital=Decimal("1000"))
    b = _sample_inputs(allocated_capital=Decimal("1000.00"))
    # tenant_id/decision 필드가 uuid4()로 서로 달라지므로 hash 비교 전 동일화한다.
    a = a.model_copy(update={"tenant_id": b.tenant_id, "as_of": now})
    b = b.model_copy(update={"as_of": now})
    assert a.inputs_hash() == b.inputs_hash()


def test_inputs_hash_changes_with_different_intent():
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    tenant_id = uuid4()
    a = _sample_inputs(tenant_id=tenant_id, as_of=now)
    b = _sample_inputs(
        tenant_id=tenant_id,
        as_of=now,
        intent=OrderIntent(
            symbol="ETH/USDT",
            asset_class="CRYPTO_SPOT",
            side="BUY",
            quantity=Decimal("1"),
            ref_price=Decimal("3000"),
            notional=Decimal("3000"),
            reduce_only=False,
            strategy_id="strat-1",
            strategy_version="1.0",
            capital_pct=Decimal("10"),
        ),
    )
    assert a.inputs_hash() != b.inputs_hash()


def test_exposure_limit_roundtrip():
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    inputs = _sample_inputs(limits=(limit,))
    assert inputs.limits == (limit,)


def test_from_legacy_dict_roundtrips_equity_and_safety_fields():
    tenant_id = uuid4()
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    allocation = _Allocation("BTC/USDT", "strat-1", Decimal("0.1"), Decimal("10"))
    account_state = {
        "daily_pnl_pct": Decimal("-2.5"),
        "drawdown_pct": Decimal("3.0"),
        "position_quantity": Decimal("0"),
        "total_equity": Decimal("10000"),
        "certified_badge": True,
        "allocated_capital": Decimal("1000"),
        "available_balance": Decimal("9000"),
        "var_pct": Decimal("1.5"),
        "correlated_exposure_pct": Decimal("5.0"),
        "recent_trade_count_1h": 3,
        "avg_trade_count_24h": 1.5,
        "circuit_breaker_level": "normal",
        "execution_paused_by_safety": False,
        "leverage": Decimal("1.0"),
    }

    inputs = RiskInputs.from_legacy_dict(
        allocation, account_state, tenant_id=tenant_id, execution_id=42, now=now
    )

    assert inputs.tenant_id == tenant_id
    assert inputs.execution_ref == "exec:42"
    assert inputs.certified_badge is True
    assert inputs.allocated_capital == Decimal("1000")
    assert inputs.intent.symbol == "BTC/USDT"
    assert inputs.intent.strategy_id == "strat-1"
    assert inputs.intent.quantity == Decimal("0.1")
    assert inputs.intent.capital_pct == Decimal("10.000000")
    assert inputs.equity.daily_pnl_pct == Decimal("-2.5")
    assert inputs.equity.drawdown_pct == Decimal("3.0")
    assert inputs.equity.total_equity == Decimal("10000")
    assert inputs.equity.available_balance == Decimal("9000")
    assert inputs.exposure.position_quantity == Decimal("0")
    assert inputs.exposure.open_positions_count == 0
    assert inputs.exposure.gross_leverage == Decimal("1.0")
    assert inputs.stats.var_pct == Decimal("1.5")
    assert inputs.stats.correlated_exposure_pct == Decimal("5.0")
    assert inputs.activity.trades_last_1h == 3
    assert inputs.activity.trades_avg_per_hour_24h == Decimal("1.5")
    assert inputs.safety.circuit_breaker_level == "normal"
    assert inputs.safety.execution_paused_by_safety is False
    assert inputs.as_of == now


def test_from_legacy_dict_missing_fields_become_none():
    tenant_id = uuid4()
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    allocation = _Allocation("BTC/USDT", "strat-1", Decimal("0.1"), Decimal("10"))
    inputs = RiskInputs.from_legacy_dict(
        allocation, {}, tenant_id=tenant_id, execution_id=1, now=now
    )

    assert inputs.equity.daily_pnl_pct is None
    assert inputs.stats.var_pct is None
    assert inputs.safety.circuit_breaker_level is None
    assert inputs.exposure.open_positions_count == 0


def test_from_legacy_dict_nonzero_position_counts_as_open():
    tenant_id = uuid4()
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    allocation = _Allocation("BTC/USDT", "strat-1", Decimal("0.1"), Decimal("10"))
    inputs = RiskInputs.from_legacy_dict(
        allocation,
        {"position_quantity": Decimal("0.5")},
        tenant_id=tenant_id,
        execution_id=1,
        now=now,
    )
    assert inputs.exposure.open_positions_count == 1
