"""L10 -- StrategyRiskParams: strategy-level stop-loss/take-profit rule (fixed
% or ATR multiple), derived into concrete price levels against a market
snapshot.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2 row 67.

Long-only: `FSMState` (src/data/models/strategy_fsm.py) has no SHORT/COVER
states -- every position this engine manages is a long spot position entered
on BUY and closed on SELL/STOP_LOSS -- so `derive_levels` always prices the
stop below `entry_price` and the take-profit above it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, model_validator

from src.core.strategy.condition_evaluator import IndicatorDataMissingError
from src.core.strategy.market_state import MarketState

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


class StrategyRiskParams(BaseModel):
    schema_version: Literal["srp-v1"] = "srp-v1"
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    atr_stop_multiple: Decimal | None = None
    atr_key: str | None = None

    @model_validator(mode="after")
    def _validate_ranges(self) -> StrategyRiskParams:
        if self.stop_loss_pct is not None and not (_ZERO < self.stop_loss_pct < _HUNDRED):
            raise ValueError("stop_loss_pct must be within (0, 100)")
        if self.take_profit_pct is not None and self.take_profit_pct <= _ZERO:
            raise ValueError("take_profit_pct must be positive")
        if self.atr_stop_multiple is not None and self.atr_stop_multiple <= _ZERO:
            raise ValueError("atr_stop_multiple must be positive")
        if (self.atr_stop_multiple is None) != (self.atr_key is None):
            raise ValueError("atr_stop_multiple and atr_key must be set together")
        if self.stop_loss_pct is not None and self.atr_stop_multiple is not None:
            raise ValueError("stop_loss_pct and atr_stop_multiple are mutually exclusive")
        return self


def derive_levels(
    params: StrategyRiskParams,
    entry_price: Decimal,
    market_state: MarketState,
) -> tuple[Decimal | None, Decimal | None]:
    """Long-only: stop priced below `entry_price`, take-profit above it.
    Returns `(stop, take_profit)`; either half is `None` when its rule is
    unset in `params`.

    Fail-closed: a configured `atr_key` missing from `market_state.values`
    (indicator not warmed up yet, or `market_state` built for the wrong
    timeframe) raises `IndicatorDataMissingError` rather than silently
    skipping the stop -- an entry without its configured protective stop is
    exactly the outcome this module exists to prevent (§1.2 audit gap:
    `stop_loss=None` constant).
    """
    if entry_price <= _ZERO:
        raise ValueError("entry_price must be positive")

    stop: Decimal | None = None
    if params.atr_stop_multiple is not None:
        if params.atr_key is None:
            raise ValueError("atr_stop_multiple set without atr_key")
        if params.atr_key not in market_state.values:
            raise IndicatorDataMissingError(params.atr_key)
        atr_value = market_state.values[params.atr_key]
        stop = entry_price - params.atr_stop_multiple * atr_value
    elif params.stop_loss_pct is not None:
        stop = entry_price * (_ONE - params.stop_loss_pct / _HUNDRED)

    if stop is not None and stop <= _ZERO:
        raise ValueError(f"computed stop price must be positive, got {stop}")

    take_profit: Decimal | None = None
    if params.take_profit_pct is not None:
        take_profit = entry_price * (_ONE + params.take_profit_pct / _HUNDRED)

    return stop, take_profit


__all__ = ["StrategyRiskParams", "derive_levels"]
