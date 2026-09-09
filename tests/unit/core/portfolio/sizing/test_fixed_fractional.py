"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 90, §8 line 572 — fixed_fractional.py tests."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputInvalidError
from src.core.portfolio.sizing.fixed_fractional import size
from src.core.portfolio.state_input import PortfolioStateInput

_HEX64 = "a" * 64


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.FIXED_FRACTIONAL,
        "fraction_pct": Decimal("20"),
        "min_trade_notional": Decimal("10"),
        "cost_model": CostModelRef(model_id="cm-1", cost_model_hash=_HEX64),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _inp(**overrides: Any) -> PortfolioStateInput:
    base: dict[str, Any] = {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9000"),
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


def test_size_matches_exact_decimal_formula():
    result = size(_inp())

    assert result.quantity == Decimal("0.004")  # 1000*20/100/50000
    assert result.weight_pct == Decimal("2")  # 200/10000*100
    assert result.method == SizingMethod.FIXED_FRACTIONAL
    assert len(result.inputs_hash) == 64


def test_size_is_deterministic_for_equal_inputs():
    assert size(_inp()).inputs_hash == size(_inp()).inputs_hash


def test_size_changes_hash_when_fraction_pct_changes():
    a = size(_inp())
    b = size(_inp(portfolio_config=_config(fraction_pct=Decimal("30"))))
    assert a.inputs_hash != b.inputs_hash


def test_size_rejects_zero_price():
    with pytest.raises(SizingInputInvalidError):
        size(_inp(current_price=Decimal("0")))


def test_size_rejects_negative_price():
    with pytest.raises(SizingInputInvalidError):
        size(_inp(current_price=Decimal("-1")))
