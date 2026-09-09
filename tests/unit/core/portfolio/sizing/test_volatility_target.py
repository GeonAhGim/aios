"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 91, §8 line 572 — volatility_target.py tests."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputMissingError
from src.core.portfolio.sizing.volatility_target import size
from src.core.portfolio.state_input import PortfolioStateInput

_HEX64 = "b" * 64


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.VOLATILITY_TARGET,
        "target_vol_pct": Decimal("10"),
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
        "realized_vol_pct": Decimal("20"),
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


def test_size_matches_exact_decimal_formula():
    result = size(_inp())

    assert result.weight_pct == Decimal("50")  # min(10/20*100, 100)
    assert result.quantity == Decimal("0.1")  # 10000*50/100/50000
    assert result.method == SizingMethod.VOLATILITY_TARGET


def test_size_caps_weight_at_100_pct():
    result = size(
        _inp(
            realized_vol_pct=Decimal("5"),
            portfolio_config=_config(target_vol_pct=Decimal("200")),
        )
    )

    assert result.weight_pct == Decimal("100")
    assert result.quantity == Decimal("0.2")  # 10000*100/100/50000


def test_size_rejects_none_realized_vol_pct():
    with pytest.raises(SizingInputMissingError) as exc_info:
        size(_inp(realized_vol_pct=None))
    assert exc_info.value.code == "PORTFOLIO_SIZING_INPUT_MISSING"


def test_size_rejects_none_target_vol_pct():
    with pytest.raises(SizingInputMissingError):
        size(_inp(portfolio_config=_config(target_vol_pct=None)))
