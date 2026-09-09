"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 90 — fixed fractional sizing.

`qty = allocated_capital × fraction% / price`. `allocated_capital`·
`current_price`·`total_equity`는 `PortfolioStateInput`이 이미 필수(Optional
아님) 필드로 강제하므로, 여기서는 0으로 나누거나 음수 비중이 나오지 않도록
`price`/`total_equity` > 0만 fail-closed로 추가 검증한다.
"""
from __future__ import annotations

from src.core.portfolio.config import SizingMethod
from src.core.portfolio.sizing import HUNDRED, SizingResult, hash_inputs, require_positive
from src.core.portfolio.state_input import PortfolioStateInput


def size(inp: PortfolioStateInput) -> SizingResult:
    require_positive(inp.current_price, "current_price")
    require_positive(inp.total_equity, "total_equity")

    cfg = inp.portfolio_config
    notional = inp.allocated_capital * cfg.fraction_pct / HUNDRED
    quantity = notional / inp.current_price
    weight_pct = notional / inp.total_equity * HUNDRED

    return SizingResult(
        quantity=quantity,
        weight_pct=weight_pct,
        method=SizingMethod.FIXED_FRACTIONAL,
        inputs_hash=hash_inputs(
            SizingMethod.FIXED_FRACTIONAL,
            allocated_capital=inp.allocated_capital,
            fraction_pct=cfg.fraction_pct,
            current_price=inp.current_price,
        ),
    )
