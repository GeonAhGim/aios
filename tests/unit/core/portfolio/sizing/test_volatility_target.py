"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 91, §8 line 572 — volatility_target.py tests."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputInvalidError, SizingInputMissingError
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


def test_size_rejects_zero_realized_vol_pct() -> None:
    """Negative: realized_vol_pct=0 is not silently treated as infinity — reject."""
    with pytest.raises(SizingInputInvalidError) as exc_info:
        size(_inp(realized_vol_pct=Decimal("0")))
    assert exc_info.value.code == "PORTFOLIO_SIZING_INPUT_INVALID"
    assert exc_info.value.field == "realized_vol_pct"


def test_size_rejects_negative_current_price() -> None:
    """Negative: negative price must be rejected (not produce negative quantity)."""
    with pytest.raises(SizingInputInvalidError) as exc_info:
        size(_inp(current_price=Decimal("-100")))
    assert exc_info.value.code == "PORTFOLIO_SIZING_INPUT_INVALID"
    assert exc_info.value.field == "current_price"


def test_size_rejects_zero_total_equity() -> None:
    """Negative: zero equity must not produce a valid position."""
    with pytest.raises(SizingInputInvalidError) as exc_info:
        size(_inp(total_equity=Decimal("0")))
    assert exc_info.value.code == "PORTFOLIO_SIZING_INPUT_INVALID"
    assert exc_info.value.field == "total_equity"


def test_size_monkeypatch_dependency_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-injection: if a downstream dependency raises, the caller propagates it."""
    original_validate = PortfolioStateInput.model_validate

    def broken_validate(data: Any) -> PortfolioStateInput:
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(
        PortfolioStateInput,
        "model_validate",
        staticmethod(broken_validate),
    )
    with pytest.raises(RuntimeError, match="database connection lost"):
        size(_inp())


def test_size_performance_under_1ms() -> None:
    """Performance: single size() call must complete well under 1 ms."""
    inp = _inp()
    iterations = 1000
    start = time.perf_counter_ns()
    for _ in range(iterations):
        size(inp)
    elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
    avg_ms = elapsed_ms / iterations
    assert avg_ms < 1.0, f"avg size() took {avg_ms:.3f} ms, budget is 1 ms"
