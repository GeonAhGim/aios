"""03_core_modules_v1.1.md#§3.5 — Signal.

Structured signal produced by FD-8.1 (StrategyEngine.evaluate). Contains
"intent" only — target_position is filled with a Draft value (0) and
overwritten by FD-8.2 (PortfolioEngine) with the actual quantity
(8.2-A Master Authority — this layer does not decide how much to buy).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide


class Signal(BaseModel):
    strategy_id: str
    strategy_version: str
    symbol: str
    direction: OrderSide
    confidence: float
    target_position: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    timestamp: datetime
    # Field not in the original 03 §3.5 — added with FD-8.0 (FSM execution
    # state tracking). The execution loop must know "which transition does
    # this signal represent?" so it can transition fsm_state to the correct
    # PENDING state (both exit and stop_loss are SELL direction in HOLDING,
    # so direction alone cannot distinguish them). Natural extension that
    # matches the original 03 description: "signal generated as a transition
    # result."
    to_state: FSMState
