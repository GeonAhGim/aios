"""109 SS5 -- thin delegation wrapper over the L27 FillSimulatorPort adapter.

Fill arithmetic itself lives in adapters/bar_fill_simulator.py
(BarFillSimulator). This free function stays so existing call sites
keeping the `simulate_fill(*, bar, bar_index, side, quantity, cost_model)`
signature do not need to change.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.adapters.bar_fill_simulator import BarFillSimulator
from src.foundation.backtest.domain.models import CostModel, SimulatedFill

_SIMULATOR = BarFillSimulator()


def simulate_fill(
    *,
    bar: Candle,
    bar_index: int,
    side: OrderSide,
    quantity: Decimal,
    cost_model: CostModel,
) -> SimulatedFill:
    return _SIMULATOR.simulate(
        bar=bar,
        bar_index=bar_index,
        side=side,
        quantity=quantity,
        cost_model=cost_model,
        seed=0,
    )
