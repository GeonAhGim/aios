"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 92, §8 line 572 — kelly_capped.py tests."""

from __future__ import annotations

from decimal import Decimal, DivisionByZero
from typing import Any

import pytest

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.sizing import SizingInputInvalidError, SizingInputMissingError
from src.core.portfolio.sizing.kelly_capped import size
from src.core.portfolio.state_input import PortfolioStateInput

_HEX64 = "c" * 64


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.KELLY_CAPPED,
        "kelly_cap_pct": Decimal("25"),
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
        "win_rate": Decimal("0.6"),
        "avg_win_loss_ratio": Decimal("2"),
        "portfolio_config": _config(),
    }
    base.update(overrides)
    return PortfolioStateInput.model_validate(base)


def test_size_matches_exact_decimal_formula_when_uncapped():
    # f* = 0.6 - 0.4/2 = 0.4, capped at kelly_cap 0.25 -> f=0.25
    result = size(_inp())

    assert result.weight_pct == Decimal("25")
    assert result.quantity == Decimal("0.05")  # 10000*0.25/50000
    assert result.method == SizingMethod.KELLY_CAPPED


def test_size_uses_uncapped_edge_when_below_cap():
    # f* = 0.55 - 0.45/3 = 0.4 < kelly_cap 0.5
    result = size(
        _inp(
            win_rate=Decimal("0.55"),
            avg_win_loss_ratio=Decimal("3"),
            portfolio_config=_config(kelly_cap_pct=Decimal("50")),
        )
    )

    assert result.weight_pct == Decimal("40")


def test_negative_kelly_clamps_to_zero():
    # f* = 0.3 - 0.7/1 = -0.4 -> clamp to 0
    result = size(_inp(win_rate=Decimal("0.3"), avg_win_loss_ratio=Decimal("1")))

    assert result.weight_pct == Decimal("0")
    assert result.quantity == Decimal("0")


def test_size_rejects_none_win_rate():
    with pytest.raises(SizingInputMissingError):
        size(_inp(win_rate=None))


def test_size_rejects_none_avg_win_loss_ratio():
    with pytest.raises(SizingInputMissingError):
        size(_inp(avg_win_loss_ratio=None))


def test_size_rejects_win_rate_out_of_range():
    with pytest.raises(SizingInputInvalidError):
        size(_inp(win_rate=Decimal("1.5")))


def _kelly_without_payoff_guard(win_rate: Decimal, payoff_ratio: Decimal) -> Decimal:
    """`require_positive(payoff_ratio, ...)` 가드를 빼먹은 회귀본 — kelly_capped.py:32
    의 가드가 실제로 `avg_win_loss_ratio=0`을 막고 있음을 대조 증명한다."""
    return win_rate - (Decimal("1") - win_rate) / payoff_ratio


def test_gate_red_missing_payoff_guard_leaks_division_by_zero():
    """`avg_win_loss_ratio=0`일 때, 가드를 뺀 회귀본은 decimal.DivisionByZero를
    그대로 새어나가게 한다(적색) — 실제 구현은 require_positive가 그 지점에
    도달하기 전에 SizingInputInvalidError로 fail-closed 거부한다(녹색)."""
    with pytest.raises(DivisionByZero):
        _kelly_without_payoff_guard(Decimal("0.6"), Decimal("0"))

    with pytest.raises(SizingInputInvalidError):
        size(_inp(avg_win_loss_ratio=Decimal("0")))
