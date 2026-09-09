"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 92 — capped Kelly sizing.

`f* = p − (1−p)/b`, `f = min(max(f*, 0), kelly_cap)`. `win_rate` (p) is a
[0,1] probability, `avg_win_loss_ratio` (b) is the average-win/average-loss
ratio — if either is `None`, this rejects rather than substituting 0/1/etc.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.portfolio.config import SizingMethod
from src.core.portfolio.sizing import (
    HUNDRED,
    SizingInputInvalidError,
    SizingResult,
    hash_inputs,
    require,
    require_positive,
)
from src.core.portfolio.state_input import PortfolioStateInput

_ZERO = Decimal("0")
_ONE = Decimal("1")


def size(inp: PortfolioStateInput) -> SizingResult:
    win_rate = require(inp.win_rate, "win_rate")
    payoff_ratio = require(inp.avg_win_loss_ratio, "avg_win_loss_ratio")
    kelly_cap_pct = require(
        inp.portfolio_config.kelly_cap_pct, "portfolio_config.kelly_cap_pct"
    )
    require_positive(payoff_ratio, "avg_win_loss_ratio")
    require_positive(inp.current_price, "current_price")
    require_positive(inp.total_equity, "total_equity")

    if not (_ZERO <= win_rate <= _ONE):
        raise SizingInputInvalidError("win_rate", "must be within [0, 1]")

    f_star = win_rate - (_ONE - win_rate) / payoff_ratio
    kelly_cap_fraction = kelly_cap_pct / HUNDRED
    f = min(max(f_star, _ZERO), kelly_cap_fraction)

    weight_pct = f * HUNDRED
    notional = inp.total_equity * f
    quantity = notional / inp.current_price

    return SizingResult(
        quantity=quantity,
        weight_pct=weight_pct,
        method=SizingMethod.KELLY_CAPPED,
        inputs_hash=hash_inputs(
            SizingMethod.KELLY_CAPPED,
            win_rate=win_rate,
            avg_win_loss_ratio=payoff_ratio,
            kelly_cap_pct=kelly_cap_pct,
            current_price=inp.current_price,
        ),
    )
