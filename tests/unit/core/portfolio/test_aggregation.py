"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 95, §9 L18 — aggregation.py tests.

DoD (a)-(e) must all be falsifiable: dropping the exact contract, breaking
the sum identity, defaulting `as_of`, or letting `total_equity<=0` reach a
`ZeroDivisionError` must each fail some test here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.portfolio.aggregation import (
    ExecutionExposure,
    MissingAsOfError,
    NonPositiveEquityError,
    aggregate,
)

_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _exposures() -> list[ExecutionExposure]:
    return [
        ExecutionExposure(
            execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("2000")
        ),
        ExecutionExposure(
            execution_id=2, strategy_id="strat-a", symbol="ETH/USDT", notional=Decimal("1000")
        ),
        ExecutionExposure(
            execution_id=3,
            strategy_id="strat-b",
            symbol="BTC/USDT",
            notional=Decimal("2000"),
            vol_pct=Decimal("15"),
        ),
    ]


# --- (a) exact public contract ------------------------------------------------


def test_execution_exposure_has_exactly_the_spec_fields():
    expected = {"execution_id", "strategy_id", "symbol", "notional", "vol_pct"}
    assert set(ExecutionExposure.model_fields.keys()) == expected


def test_execution_exposure_vol_pct_defaults_to_none():
    exposure = ExecutionExposure(
        execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("100")
    )
    assert exposure.vol_pct is None


# --- (b) sum identities, exact Decimal ----------------------------------------


def test_sum_identity_symbol_strategy_and_total_match_exactly():
    agg = aggregate(_exposures(), cash=Decimal("5000"), as_of=_AS_OF)

    assert sum(agg.per_symbol_pct.values(), Decimal("0")) == agg.total_exposure_pct
    assert sum(agg.per_strategy_pct.values(), Decimal("0")) == agg.total_exposure_pct
    assert agg.cash_pct + agg.total_exposure_pct == Decimal("100")


def test_aggregate_values_are_exact_decimals():
    # total_equity = 5000 cash + 5000 notional = 10000
    agg = aggregate(_exposures(), cash=Decimal("5000"), as_of=_AS_OF)

    assert agg.total_equity == Decimal("10000")
    assert agg.per_symbol_pct["BTC/USDT"] == Decimal("40")  # 4000/10000*100
    assert agg.per_symbol_pct["ETH/USDT"] == Decimal("10")  # 1000/10000*100
    assert agg.per_strategy_pct["strat-a"] == Decimal("30")  # 3000/10000*100
    assert agg.per_strategy_pct["strat-b"] == Decimal("20")  # 2000/10000*100
    assert agg.total_exposure_pct == Decimal("50")
    assert agg.cash_pct == Decimal("50")
    assert agg.as_of == _AS_OF


def test_aggregate_with_no_exposures_is_all_cash():
    agg = aggregate([], cash=Decimal("1000"), as_of=_AS_OF)

    assert agg.total_equity == Decimal("1000")
    assert agg.per_symbol_pct == {}
    assert agg.per_strategy_pct == {}
    assert agg.total_exposure_pct == Decimal("0")
    assert agg.cash_pct == Decimal("100")


# --- (c) as_of is mandatory, never defaulted ----------------------------------


def test_aggregate_rejects_as_of_none_instead_of_defaulting():
    with pytest.raises(MissingAsOfError):
        aggregate(_exposures(), cash=Decimal("5000"), as_of=None)  # type: ignore[arg-type]


# --- (d) total_equity<=0 is a defined rejection, not ZeroDivisionError -------


def test_aggregate_rejects_zero_total_equity():
    with pytest.raises(NonPositiveEquityError):
        aggregate([], cash=Decimal("0"), as_of=_AS_OF)


def test_aggregate_rejects_negative_total_equity():
    exposures = [
        ExecutionExposure(
            execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=Decimal("-500")
        )
    ]
    with pytest.raises(NonPositiveEquityError):
        aggregate(exposures, cash=Decimal("100"), as_of=_AS_OF)


# --- Decimal-only enforcement (105 standard) ----------------------------------


def test_execution_exposure_rejects_float_notional():
    with pytest.raises(ValidationError):
        ExecutionExposure(execution_id=1, strategy_id="strat-a", symbol="BTC/USDT", notional=1.5)


def test_execution_exposure_rejects_float_vol_pct():
    with pytest.raises(ValidationError):
        ExecutionExposure(
            execution_id=1,
            strategy_id="strat-a",
            symbol="BTC/USDT",
            notional=Decimal("100"),
            vol_pct=15.0,
        )
