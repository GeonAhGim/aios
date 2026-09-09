"""L26 -- FillSimulatorPort: a fill-simulator contract shared with PAPER execution.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (ports/fill_simulator.py row).
The future src/exchanges/paper_sim/adapter.py (a separate L4) must implement this Protocol.

Only the Protocol declaration and `...` bodies belong here -- no I/O,
logging, or default implementation (L26 DoD (b)).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import CostModel, SimulatedFill

__all__ = ["FillSimulatorPort"]


@runtime_checkable
class FillSimulatorPort(Protocol):
    def simulate(
        self,
        *,
        bar: Candle,
        bar_index: int,
        side: OrderSide,
        quantity: Decimal,
        cost_model: CostModel,
        seed: int,
    ) -> SimulatedFill: ...
