"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 97, section 9 L21 --
rebalance.py tests.

DoD (c)/(d)/(e) must all be falsifiable: trading inside the band, dropping
`min_trade_notional`'s exact-boundary behaviour, or approximating
`turnover_pct`/`est_cost` instead of exact `Decimal` arithmetic must each
fail some test here.
"""

from __future__ import annotations

import time
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


# --- D2: Decimal-only enforcement (105 standard) -- float inputs reject ------


def test_plan_rebalance_rejects_float_target_weight():
    """`targets` is a plain dict, not a pydantic-validated field, so the
    Decimal-only guarantee here comes from `Decimal.__sub__` refusing to mix
    with `float` -- a float target must fail loud (TypeError), never get
    silently computed with float contamination (105 standard)."""
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    with pytest.raises(TypeError):
        plan_rebalance(
            {"BTC/USDT": 45.0}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
        )


def test_plan_rebalance_rejects_float_price():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    with pytest.raises(TypeError):
        plan_rebalance(
            {"BTC/USDT": Decimal("60")}, current, cfg, {"BTC/USDT": 20000.0}, Decimal("2")
        )


# --- D2: failure injection (ADR-2026-09-09-C Decision 1) ---------------------


class _CorruptedSymbolMap(dict[str, Decimal]):
    """Simulates a broken upstream feed: aggregation.py's `aggregate()`
    output corrupted mid-transit, so iterating `per_symbol_pct` raises
    instead of silently yielding an empty/partial symbol set."""

    def __iter__(self):
        raise ConnectionError("aggregation feed truncated mid-read")


def test_failure_injection_plan_rebalance_propagates_corrupted_aggregate_feed():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = PortfolioAggregate.model_construct(
        total_equity=Decimal("10000"),
        per_symbol_pct=_CorruptedSymbolMap({"BTC/USDT": Decimal("40")}),
        per_strategy_pct={},
        total_exposure_pct=Decimal("40"),
        cash_pct=Decimal("60"),
        as_of=_AS_OF,
    )

    with pytest.raises(ConnectionError):
        plan_rebalance(
            {"BTC/USDT": Decimal("45")}, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2")
        )


# --- D2: numeric performance assertion (pre-trade gate budget, ADR-2026-09-09-C) --


@pytest.mark.perf
def test_perf_plan_rebalance_p99_within_pre_trade_gate_budget():
    """plan_rebalance() produces the trade list that precedes order
    submission, so it sits on the same pre-trade gate path as L18's
    aggregate() -- the "사전거래 게이트 p99 5ms" budget (ADR-2026-09-09-C
    Decision 1) applies, sized to a 200-symbol portfolio.
    """
    cfg = _config(rebalance_band_pct=Decimal("1"), min_trade_notional=Decimal("1"))
    symbols = [f"SYM{i}/USDT" for i in range(200)]
    current = _aggregate({s: Decimal("0.4") for s in symbols}, total_equity=Decimal("1000000"))
    targets = {s: Decimal("0.6") for s in symbols}
    prices = {s: Decimal("100") for s in symbols}

    latencies_ms: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        plan_rebalance(targets, current, cfg, prices, Decimal("2"))
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(len(latencies_ms) * 0.99) - 1]
    assert p99 < 5.0, f"p99 {p99:.3f}ms exceeds pre-trade gate budget of 5ms (ADR-2026-09-09-C)"


# --- D2: gate-red reproduction -------------------------------------------------


def _broken_plan_rebalance_band_off_by_one(
    targets: dict[str, Decimal],
    current: PortfolioAggregate,
    cfg: PortfolioConfig,
) -> list[str]:
    """Gate-red reproduction: a regression using `<` instead of the real
    `plan_rebalance`'s `<=` for the rebalance-band comparison would wrongly
    trade a delta sitting exactly on the band boundary -- contrast with the
    real implementation's correct exclusion below."""
    symbols = sorted(set(targets) | set(current.per_symbol_pct))
    traded: list[str] = []
    for symbol in symbols:
        target_w = targets.get(symbol, Decimal("0"))
        current_w = current.per_symbol_pct.get(symbol, Decimal("0"))
        abs_delta = abs(target_w - current_w)
        if abs_delta < cfg.rebalance_band_pct:  # BUG: should be <=
            continue
        traded.append(symbol)
    return traded


def test_gate_red_band_boundary_off_by_one_would_wrongly_trade():
    cfg = _config(rebalance_band_pct=Decimal("5"))
    current = _aggregate({"BTC/USDT": Decimal("40")})
    targets = {"BTC/USDT": Decimal("45")}  # delta == band, exactly on the boundary

    red_traded = _broken_plan_rebalance_band_off_by_one(targets, current, cfg)
    assert red_traded == ["BTC/USDT"]  # red: wrongly trades the boundary

    plan = plan_rebalance(targets, current, cfg, {"BTC/USDT": Decimal("20000")}, Decimal("2"))
    assert plan.trades == []  # green: boundary correctly stays in the no-trade band
