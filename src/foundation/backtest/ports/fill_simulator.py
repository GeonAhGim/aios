"""L26 -- FillSimulatorPort: PAPER 실행과 공유하는 체결 시뮬레이터 계약.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L26 (ports/fill_simulator.py 행).
미래 src/exchanges/paper_sim/adapter.py(별도 L4)는 이 Protocol을 구현해야 한다.

Protocol 선언과 `...` 본문만 둔다 -- I/O·로깅·기본 구현 금지(L26 DoD (b)).
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
