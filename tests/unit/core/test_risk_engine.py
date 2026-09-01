from decimal import Decimal

import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.engine import RiskEngine


@pytest.fixture
def policy():
    return load_risk_policy()


@pytest.fixture
def allocation():
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-1",
        approved_quantity=Decimal("0.02"),
        capital_pct=Decimal("10"),
    )


def _valid_account_state(**overrides):
    base = {
        "daily_pnl_pct": Decimal("-1"),
        "drawdown_pct": Decimal("2"),
        "position_quantity": Decimal("0"),
        "total_equity": Decimal("10000"),
        "certified_badge": False,
        "allocated_capital": Decimal("1000"),
        "available_balance": Decimal("10000"),
        "var_pct": Decimal("1"),
        "correlated_exposure_pct": Decimal("5"),
        "recent_trade_count_1h": 1,
        "avg_trade_count_24h": 5.0,
        "circuit_breaker_level": "normal",
        "execution_paused_by_safety": False,
    }
    base.update(overrides)
    return base


def test_all_indicators_pass_approves(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state())

    assert result.approved is True
    assert result.rejection_reason is None
    assert result.checked_rules == [
        "daily_loss",
        "max_drawdown",
        "leverage",
        "position_concentration",
        "strategy_allocation",
        "var",
        "correlation_risk",
        "trade_frequency",
        "safety_state",
    ]


def test_daily_loss_rejects_and_short_circuits(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(daily_pnl_pct=Decimal("-5")))

    assert result.approved is False
    assert result.rejection_reason == "daily_loss_halt_exceeded"
    assert result.checked_rules == ["daily_loss"]


def test_max_drawdown_rejects(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(drawdown_pct=Decimal("15")))

    assert result.approved is False
    assert result.rejection_reason == "max_drawdown_hard_stop_exceeded"


def test_position_concentration_rejects_on_entry(policy, allocation):
    engine = RiskEngine(policy)
    over_limit_allocation = allocation.model_copy(update={"capital_pct": Decimal("25")})

    result = engine.check(
        over_limit_allocation, _valid_account_state(position_quantity=Decimal("0"))
    )

    assert result.approved is False
    assert result.rejection_reason == "position_concentration_exceeded"


def test_position_concentration_skipped_on_exit(policy, allocation):
    engine = RiskEngine(policy)
    exit_allocation = allocation.model_copy(update={"capital_pct": Decimal("25")})

    result = engine.check(
        exit_allocation, _valid_account_state(position_quantity=Decimal("0.1"))
    )

    assert result.approved is True


def test_strategy_allocation_rejects_when_over_cap(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(
            certified_badge=False,
            allocated_capital=Decimal("2000"),
            available_balance=Decimal("10000"),
        ),
    )

    assert result.approved is False
    assert result.rejection_reason == "strategy_allocation_exceeded"


def test_var_rejects_when_exceeded(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(var_pct=Decimal("10")))

    assert result.approved is False
    assert result.rejection_reason == "var_exceeded"


def test_correlation_risk_rejects_when_exceeded(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(allocation, _valid_account_state(correlated_exposure_pct=Decimal("40")))

    assert result.approved is False
    assert result.rejection_reason == "correlation_risk_exceeded"


def test_trade_frequency_rejects_anomaly(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(recent_trade_count_1h=100, avg_trade_count_24h=5.0),
    )

    assert result.approved is False
    assert result.rejection_reason == "trade_frequency_anomaly"


def test_trade_frequency_zero_baseline_does_not_reject(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation,
        _valid_account_state(recent_trade_count_1h=3, avg_trade_count_24h=0.0),
    )

    assert result.approved is True


def test_safety_state_rejects_when_circuit_breaker_restricted(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(circuit_breaker_level="restricted")
    )

    assert result.approved is False
    assert result.rejection_reason == "safety_state_blocked"


def test_safety_state_rejects_when_execution_paused_by_safety(policy, allocation):
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(execution_paused_by_safety=True)
    )

    assert result.approved is False
    assert result.rejection_reason == "safety_state_blocked"


def test_safety_state_checked_last_even_if_it_would_reject(policy, allocation):
    """다른 7개 지표를 전부 통과해도 Circuit Breaker RESTRICTED 이상이면
    거부되는지 확인 — FD-8.3 완료조건."""
    engine = RiskEngine(policy)

    result = engine.check(
        allocation, _valid_account_state(circuit_breaker_level="emergency")
    )

    assert result.approved is False
    assert result.checked_rules[-1] == "safety_state"


@pytest.mark.parametrize(
    "missing_key",
    [
        "daily_pnl_pct",
        "drawdown_pct",
        "total_equity",
        "var_pct",
        "correlated_exposure_pct",
        "recent_trade_count_1h",
        "circuit_breaker_level",
    ],
)
def test_missing_data_rejects_not_approves(policy, allocation, missing_key):
    """판단 불가를 승인으로 취급하지 않는다 — Master Authority 핵심 원칙."""
    engine = RiskEngine(policy)
    state = _valid_account_state()
    state[missing_key] = None

    result = engine.check(allocation, state)

    assert result.approved is False
    assert result.rejection_reason is not None and result.rejection_reason.endswith(
        "data_unavailable"
    )
