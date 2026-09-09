"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 94, §8 line 572 — selector.py tests."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingResult, fixed_fractional
from src.core.portfolio.sizing.selector import (
    _DISPATCH,
    SizingResultTamperedError,
    UnknownSizingMethodError,
    size_for,
)
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput

_HEX64 = "e" * 64
_AS_OF = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _config(method: SizingMethod, **overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": method,
        "fraction_pct": Decimal("20"),
        "target_vol_pct": Decimal("10"),
        "kelly_cap_pct": Decimal("25"),
        "min_trade_notional": Decimal("10"),
        "cost_model": CostModelRef(model_id="cm-1", cost_model_hash=_HEX64),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


def _inp(method: SizingMethod, **overrides: Any) -> PortfolioStateInput:
    base: dict[str, Any] = {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9000"),
        "realized_vol_pct": Decimal("20"),
        "win_rate": Decimal("0.6"),
        "avg_win_loss_ratio": Decimal("2"),
        "exposures": PortfolioAggregate(
            total_equity=Decimal("10000"),
            per_symbol_pct={},
            per_strategy_pct={},
            total_exposure_pct=Decimal("0"),
            cash_pct=Decimal("100"),
            as_of=_AS_OF,
        ),
        "portfolio_config": _config(method),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


@pytest.mark.parametrize(
    "method", [m for m in SizingMethod], ids=[m.value for m in SizingMethod]
)
def test_size_for_dispatches_to_the_matching_module(method: SizingMethod):
    cfg = _config(method)
    inp = _inp(method)

    dispatched = size_for(cfg, inp)
    direct = _DISPATCH[method](inp)

    assert dispatched == direct
    assert dispatched.method == method


def test_size_for_rejects_config_input_method_mismatch():
    cfg = _config(SizingMethod.FIXED_FRACTIONAL)
    inp = _inp(SizingMethod.VOLATILITY_TARGET)

    with pytest.raises(SizingResultTamperedError):
        size_for(cfg, inp)


def test_size_for_rejects_unknown_method(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, None)

    with pytest.raises(UnknownSizingMethodError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))


def test_size_for_rejects_malformed_inputs_hash(monkeypatch: pytest.MonkeyPatch):
    def _tampered(inp: PortfolioStateInput) -> SizingResult:
        result = fixed_fractional.size(inp)
        return result.model_copy(update={"inputs_hash": "not-a-hash"})

    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, _tampered)

    with pytest.raises(SizingResultTamperedError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))


def test_size_for_rejects_result_method_mismatch(monkeypatch: pytest.MonkeyPatch):
    def _wrong_method(inp: PortfolioStateInput) -> SizingResult:
        result = fixed_fractional.size(inp)
        return result.model_copy(update={"method": SizingMethod.KELLY_CAPPED})

    monkeypatch.setitem(_DISPATCH, SizingMethod.FIXED_FRACTIONAL, _wrong_method)

    with pytest.raises(SizingResultTamperedError):
        size_for(_config(SizingMethod.FIXED_FRACTIONAL), _inp(SizingMethod.FIXED_FRACTIONAL))
