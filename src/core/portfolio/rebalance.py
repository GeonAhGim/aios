"""L4_strategy_portfolio_backtest_v1.0.md#section 2 row 97, section 9 L21 --
rebalance.py.

Turns a set of target weights into a trade list: a pure function of the
current `PortfolioAggregate` (L18), the `PortfolioConfig` band/threshold
(L17) and current prices.

`PortfolioConfig.cost_model` is a `CostModelRef` (`model_id` +
`cost_model_hash` only, see `config.py`) -- by that type's own contract, a
consumer that needs the actual numeric cost resolves it "by `model_id` in
the module that owns it" (the real `CostModel` bps live in
`foundation.backtest.domain.models`, a SCAFFOLD-zone module this
FROZEN_PAPER_ONLY leaf must not import). So `plan_rebalance` takes the
already-resolved `cost_rate` as an explicit argument instead of reaching
into `cfg.cost_model` for a rate that field does not carry.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from src.core.portfolio.config import PortfolioConfig
from src.core.portfolio.state_input import PortfolioAggregate
from src.data.models.trading import OrderSide

_HUNDRED = Decimal("100")
_TWO = Decimal("2")
_ZERO = Decimal("0")


class RebalanceError(ValueError):
    """A `plan_rebalance` input violated the contract this module enforces."""


class RebalancePriceMissingError(RebalanceError):
    """A symbol crossed the rebalance band (and the notional threshold) but
    has no entry in `prices` -- there is no safe default price to size the
    trade with, so the plan is refused rather than silently skipping it."""


class TradeLeg(BaseModel):
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    notional: Decimal
    delta_weight_pct: Decimal


class RebalancePlan(BaseModel):
    trades: list[TradeLeg]
    turnover_pct: Decimal
    est_cost: Decimal
    skipped: list[str]


def plan_rebalance(
    targets: dict[str, Decimal],
    current: PortfolioAggregate,
    cfg: PortfolioConfig,
    prices: dict[str, Decimal],
    cost_rate: Decimal,
) -> RebalancePlan:
    """Build a `RebalancePlan` from target weights vs. the current aggregate.

    For every symbol appearing in either `targets` or `current.per_symbol_pct`
    (missing on one side treated as a 0% weight), `delta_w = target - current`:

    - `|delta_w] <= cfg.rebalance_band_pct` -- inside the no-trade band, no
      trade, recorded in `skipped` as `"{symbol}:REBALANCE_BAND"`.
    - otherwise the trade notional is `|delta_w| / 100 * current.total_equity`;
      if that is below `cfg.min_trade_notional` it is dropped and recorded as
      `"{symbol}:MIN_TRADE_NOTIONAL"`.
    - otherwise it becomes a `TradeLeg` (BUY if `delta_w > 0` else SELL).

    `turnover_pct` sums `|delta_w|` only over symbols that actually produced
    a `TradeLeg` (a skipped delta was never traded, so it must not inflate
    turnover or `est_cost`), divided by two per the standard two-sided
    turnover definition. `est_cost = turnover_pct * cost_rate` exactly, in
    `Decimal` -- no rounding step is introduced.

    Symbols are visited in sorted order so the result (and any error) is
    deterministic regardless of dict insertion order (reproducibility, R1).
    """
    symbols = sorted(set(targets) | set(current.per_symbol_pct))
    trades: list[TradeLeg] = []
    skipped: list[str] = []
    turnover_sum = _ZERO

    for symbol in symbols:
        target_w = targets.get(symbol, _ZERO)
        current_w = current.per_symbol_pct.get(symbol, _ZERO)
        delta_w = target_w - current_w
        abs_delta = abs(delta_w)

        if abs_delta <= cfg.rebalance_band_pct:
            skipped.append(f"{symbol}:REBALANCE_BAND")
            continue

        notional = abs_delta / _HUNDRED * current.total_equity
        if notional < cfg.min_trade_notional:
            skipped.append(f"{symbol}:MIN_TRADE_NOTIONAL")
            continue

        price = prices.get(symbol)
        if price is None:
            raise RebalancePriceMissingError(
                f"no price for {symbol} -- cannot size a trade the band/threshold checks require"
            )

        trades.append(
            TradeLeg(
                symbol=symbol,
                side=OrderSide.BUY if delta_w > 0 else OrderSide.SELL,
                quantity=notional / price,
                price=price,
                notional=notional,
                delta_weight_pct=delta_w,
            )
        )
        turnover_sum += abs_delta

    turnover_pct = turnover_sum / _TWO
    est_cost = turnover_pct * cost_rate
    return RebalancePlan(
        trades=trades, turnover_pct=turnover_pct, est_cost=est_cost, skipped=skipped
    )
