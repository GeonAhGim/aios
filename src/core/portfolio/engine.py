"""FD-8.2 — Portfolio allocation decision (PortfolioEngine).

Spec: 기능설계문서_v1.21.md#FD-8.2, 03_core_modules_v1.1.md#§3.6

The capital allocation limit is already fixed at FD-16.1 (execution creation time) — this
class only computes "how much to allocate for this signal" within that limit (not a limit
reassessment). Phase 1 does not support partial liquidation — SELL always closes the full position.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.portfolio.models import AllocationDecision
from src.core.strategy.models import Signal
from src.data.models.trading import OrderSide


class PortfolioEngineError(Exception):
    """FD-8.2 exceptional state — a state combination that the parent (FD-8.1) should never
    produce in normal flow (SELL signal with no position, or re-entry BUY signal while already
    holding). If this occurs, the bug is in FD-8.1 logic, not the Executor."""


class PortfolioEngine:
    def allocate(
        self, signal: Signal, current_portfolio_state: dict[str, Any]
    ) -> AllocationDecision | None:
        """Required keys in `current_portfolio_state`:

        - `allocated_capital: Decimal` — FD-16.1 capital allocation limit for this execution
        - `position_quantity: Decimal` — current held quantity (0 means no position)
        - `current_price: Decimal | None` — current price at signal generation time
          (None on lookup failure — signals to skip this tick)
        - `total_equity: Decimal` — total account equity (used for capital_pct calculation)

        Section 3.6 of 03_core_modules specifies the return type as `AllocationDecision`
        (non-Optional), but to satisfy the FD-8.2 exception ("skip this tick when current
        price lookup fails"), it must actually be Optional — an extension of the same principle
        by which FD-8.1's `evaluate()` already returns `Signal | None` for the same reason.
        """
        current_price = current_portfolio_state.get("current_price")
        if current_price is None or current_price <= 0:
            return None  # Do not estimate quantity without price info — retry on next tick

        position_quantity: Decimal = current_portfolio_state["position_quantity"]
        allocated_capital: Decimal = current_portfolio_state["allocated_capital"]
        total_equity: Decimal = current_portfolio_state["total_equity"]

        if signal.direction == OrderSide.BUY:
            if position_quantity != 0:
                raise PortfolioEngineError(
                    "이미 보유 포지션이 있는 상태에서 진입(BUY) 신호가 발생했습니다 — "
                    "IDLE에서만 entry 전이가 나와야 하므로 FD-8.1 로직 오류입니다."
                )
            approved_quantity = allocated_capital / current_price
        else:
            if position_quantity == 0:
                raise PortfolioEngineError(
                    "포지션이 없는 상태에서 SELL(exit/stop_loss) 신호가 발생했습니다 — "
                    "FD-8.1 로직 오류입니다."
                )
            approved_quantity = position_quantity  # Phase 1 does not support partial liquidation — full close

        capital_pct = (approved_quantity * current_price) / total_equity * Decimal("100")

        return AllocationDecision(
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            approved_quantity=approved_quantity,
            capital_pct=capital_pct,
        )
