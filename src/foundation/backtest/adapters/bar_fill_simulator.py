"""L27 -- BarFillSimulator: FillSimulatorPort adapter over bar OHLC data.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#L27.
Carries forward the exact fill arithmetic that used to live directly in
application/simulate_fill.py, now behind the L26 FillSimulatorPort
contract (ports/fill_simulator.py) so PAPER execution can share it later.

Fill price uses the given bar's open (109 SS5 -- callers must pass "the
bar after the signal bar"). `signal_bar_index`, when given, is checked
against I-05 look-ahead safety via domain/rules.is_look_ahead_safe --
this adapter does not define a new violation error.
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import CostModel, SimulatedFill
from src.foundation.backtest.domain.rules import (
    LookaheadViolationError,
    is_look_ahead_safe,
)

_BPS = Decimal("10000")


class BarFillSimulator:
    """Linear bps slippage/fee model, satisfying FillSimulatorPort."""

    def simulate(
        self,
        *,
        bar: Candle,
        bar_index: int,
        side: OrderSide,
        quantity: Decimal,
        cost_model: CostModel,
        seed: int,
        signal_bar_index: int | None = None,
    ) -> SimulatedFill:
        del seed  # this bps model has no random component; kept for port parity
        if quantity <= 0:
            raise ValueError(f"quantity는 0보다 커야 한다: {quantity}")
        if cost_model.slippage_bps < 0:
            raise ValueError(
                f"slippage_bps는 음수를 허용하지 않는다: {cost_model.slippage_bps}"
            )
        if signal_bar_index is not None and not is_look_ahead_safe(
            signal_bar_index=signal_bar_index, fill_bar_index=bar_index
        ):
            raise LookaheadViolationError(
                signal_bar_index=signal_bar_index, fill_bar_index=bar_index
            )

        base_price = bar.open
        slippage_direction = 1 if side == OrderSide.BUY else -1
        effective_price = base_price * (
            Decimal(1) + slippage_direction * cost_model.slippage_bps / _BPS
        )
        slippage_cost = abs(effective_price - base_price) * quantity
        fee = effective_price * quantity * cost_model.fee_bps / _BPS

        return SimulatedFill(
            bar_index=bar_index,
            timestamp=bar.open_time,
            symbol=bar.symbol,
            side=side,
            price=effective_price,
            quantity=quantity,
            fee=fee,
            slippage_cost=slippage_cost,
        )
