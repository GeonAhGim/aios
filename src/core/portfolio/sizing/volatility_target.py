"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 91 — volatility-target sizing.

`weight = target_vol_pct / realized_vol_pct`(상한 100%). `realized_vol_pct`가
`None`이면 0으로 대체하지 않고 `PORTFOLIO_SIZING_INPUT_MISSING`으로 거부한다
(스펙에 명시된 negative case).
"""
from __future__ import annotations

from src.core.portfolio.config import SizingMethod
from src.core.portfolio.sizing import (
    HUNDRED,
    SizingResult,
    hash_inputs,
    require,
    require_positive,
)
from src.core.portfolio.state_input import PortfolioStateInput


def size(inp: PortfolioStateInput) -> SizingResult:
    realized_vol_pct = require(inp.realized_vol_pct, "realized_vol_pct")
    require_positive(realized_vol_pct, "realized_vol_pct")
    target_vol_pct = require(
        inp.portfolio_config.target_vol_pct, "portfolio_config.target_vol_pct"
    )
    require_positive(inp.current_price, "current_price")
    require_positive(inp.total_equity, "total_equity")

    weight_pct = min(target_vol_pct / realized_vol_pct * HUNDRED, HUNDRED)
    notional = inp.total_equity * weight_pct / HUNDRED
    quantity = notional / inp.current_price

    return SizingResult(
        quantity=quantity,
        weight_pct=weight_pct,
        method=SizingMethod.VOLATILITY_TARGET,
        inputs_hash=hash_inputs(
            SizingMethod.VOLATILITY_TARGET,
            target_vol_pct=target_vol_pct,
            realized_vol_pct=realized_vol_pct,
            current_price=inp.current_price,
        ),
    )
