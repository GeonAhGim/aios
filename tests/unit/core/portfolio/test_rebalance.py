"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 97, section 9 L21 --
rebalance.py tests.

DoD (c)/(d)/(e) must all be falsifiable: trading inside the band, dropping
`min_trade_notional`'s exact-boundary behaviour, or approximating
`turnover_pct`/`est_cost` instead of exact `Decimal` arithmetic must each
fail some test here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.rebalance import (
    RebalancePriceMissingError,
    plan_rebalance,
)
from src.core.portfolio.state_input import PortfolioAggregate
from src.data.models.trading import OrderSide

_HEX64 = "a" * 64
_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _cost_model_ref() -> CostModelRef:
    return CostModelRef(model_id="cm-1", cost_model_hash=_HEX64)


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.FIXED_FRACTIONAL,
        "rebalance_band_pct": Decimal("5"),
        "min_trade_notional": Decimal("50"),
        "cost_model": _cost_model_ref(),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _aggregate(
    per_symbol_pct: dict[str, Decimal], total_equity: Decimal = Decimal("10000")
) -> PortfolioAggregate:
    total_exposure = sum(per_symbol_pct.values(), Decimal("0"))
    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct,
        per_strategy_pct={},
        total_exposure_pct=total_exposure,
        cash_pct=Decimal("100") - total_exposure,
        as_of=_AS_OF,
    )


# --- (c) band boundary: exactly at the band is a no-trade, just above trades -


def test_delta_exactly_at_band_is_not_traded():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    plan = plan_rebalance(
        {"BTC/USDT": Decimal("45")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
    )
    assert plan.trades == []
    assert plan.skipped == ["BTC/USDT:REBALANCE_BAND"]


def test_delta_just_over_band_is_traded():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    plan = plan_rebalance(
        {"BTC/USDT": Decimal("45.01")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
    )
    assert plan.skipped == []
    assert len(plan.trades) == 1
    leg = plan.trades[0]
    assert leg.symbol == "BTC/USDT"
    assert leg.side == OrderSide.BUY
    assert leg.delta_weight_pct == Decimal("5.01")
    assert leg.notional == Decimal("501.00")
    assert leg.quantity == Decimal("501.00") / Decimal("20000")


# --- (d) min_trade_notional boundary: below excluded, at/above included -----


def test_notional_just_below_min_trade_notional_is_skipped():
    cfg = _config(rebalance_band_pct=Decimal("0"), min_trade_notional=Decimal("50"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    plan = plan_rebalance(
        {"BTC/USDT": Decimal("40.4")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
    )
    assert plan.trades == []
    assert plan.skipped == ["BTC/USDT:MIN_TRADE_NOTIONAL"]


def test_notional_at_min_trade_notional_is_included():
    cfg = _config(rebalance_band_pct=Decimal("0"), min_trade_notional=Decimal("50"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    plan = plan_rebalance(
        {"BTC/USDT": Decimal("40.5")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
    )
    assert plan.skipped == []
    assert len(plan.trades) == 1
    assert plan.trades[0].notional == Decimal("50.0")


# --- (e) turnover_pct / est_cost exact Decimal, hand-computed ---------------


def test_turnover_and_est_cost_are_exact_and_exclude_skipped_symbols():
    cfg = _config(rebalance_band_pct=Decimal("5"), min_trade_notional=Decimal("50"))
    current = _aggregate({"BTC/USDT": Decimal("40"), "ETH/USDT": Decimal("10")})
    targets = {
        "BTC/USDT": Decimal("45.01"),  # delta 5.01 -> traded
        "ETH/USDT": Decimal("10"),  # delta 0 -> band, skipped
        "SOL/USDT": Decimal("8"),  # delta 8 (vs 0) -> traded
    }
    prices = {"BTC/USDT": Decimal("20000"), "SOL/USDT": Decimal("100")}

    plan = plan_rebalance(targets, current, cfg, prices, Decimal("2"))

    assert plan.skipped == ["ETH/USDT:REBALANCE_BAND"]
    assert {leg.symbol for leg in plan.trades} == {"BTC/USDT", "SOL/USDT"}

    # hand computation: turnover = (|5.01| + |8|) / 2 = 6.505
    expected_turnover = (Decimal("5.01") + Decimal("8")) / Decimal("2")
    assert plan.turnover_pct == expected_turnover
    assert plan.est_cost == expected_turnover * Decimal("2")


def test_sell_side_when_target_is_below_current():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    plan = plan_rebalance(
        {"BTC/USDT": Decimal("20")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
    )
    assert len(plan.trades) == 1
    leg = plan.trades[0]
    assert leg.side == OrderSide.SELL
    assert leg.delta_weight_pct == Decimal("-20")


def test_trades_are_sorted_by_symbol_regardless_of_input_order():
    cfg = _config(rebalance_band_pct=Decimal("0"))
    current = _aggregate({"ZED/USDT": Decimal("0"), "ABC/USDT": Decimal("0")})
    targets = {"ZED/USDT": Decimal("10"), "ABC/USDT": Decimal("10")}
    prices = {"ZED/USDT": Decimal("10"), "ABC/USDT": Decimal("10")}

    plan = plan_rebalance(targets, current, cfg, prices, Decimal("1"))

    assert [leg.symbol for leg in plan.trades] == ["ABC/USDT", "ZED/USDT"]


# --- price missing for a required trade is a defined rejection -------------


def test_missing_price_for_a_required_trade_raises():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    with pytest.raises(RebalancePriceMissingError):
        plan_rebalance({"BTC/USDT": Decimal("60")}, current, cfg, {}, Decimal("2"))


def test_no_targets_and_no_current_exposure_is_a_no_op_plan():
    cfg = _config()
    current = _aggregate({})
    plan = plan_rebalance({}, current, cfg, {}, Decimal("2"))
    assert plan.trades == []
    assert plan.skipped == []
    assert plan.turnover_pct == Decimal("0")
    assert plan.est_cost == Decimal("0")
