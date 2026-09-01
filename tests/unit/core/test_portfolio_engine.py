from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.core.portfolio.engine import PortfolioEngine, PortfolioEngineError
from src.core.strategy.models import Signal
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide


def _signal(direction: OrderSide) -> Signal:
    to_state = (
        FSMState.BUY_ORDER_PENDING if direction == OrderSide.BUY else FSMState.SELL_ORDER_PENDING
    )
    return Signal(
        strategy_id="strat-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        direction=direction,
        confidence=1.0,
        target_position=Decimal("0"),
        stop_loss=None,
        take_profit=None,
        timestamp=datetime.now(timezone.utc),
        to_state=to_state,
    )


def test_entry_computes_quantity_from_allocated_capital_and_price():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": Decimal("50000"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is not None
    assert decision.approved_quantity == Decimal("1000") / Decimal("50000")
    assert decision.capital_pct == Decimal("10")  # 1000/10000*100


def test_exit_liquidates_full_position():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.SELL),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0.5"),
            "current_price": Decimal("50000"),
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is not None
    assert decision.approved_quantity == Decimal("0.5")


def test_missing_current_price_skips_tick_returns_none():
    engine = PortfolioEngine()

    decision = engine.allocate(
        _signal(OrderSide.BUY),
        {
            "allocated_capital": Decimal("1000"),
            "position_quantity": Decimal("0"),
            "current_price": None,
            "total_equity": Decimal("10000"),
        },
    )

    assert decision is None


def test_entry_with_existing_position_raises_logic_error():
    engine = PortfolioEngine()

    with pytest.raises(PortfolioEngineError):
        engine.allocate(
            _signal(OrderSide.BUY),
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0.1"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("10000"),
            },
        )


def test_exit_without_position_raises_logic_error():
    engine = PortfolioEngine()

    with pytest.raises(PortfolioEngineError):
        engine.allocate(
            _signal(OrderSide.SELL),
            {
                "allocated_capital": Decimal("1000"),
                "position_quantity": Decimal("0"),
                "current_price": Decimal("50000"),
                "total_equity": Decimal("10000"),
            },
        )
