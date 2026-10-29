"""BT-19 I-05 replay check: a real `run_backtest` replay vs a recorded PAPER trace.

The sibling `test_parity_harness.py` compares hand-built sequences; this file
closes the gap by feeding the harness fills produced by an actual
`run_backtest` run over a fixed artifact (FSM config + bars) and diffing them
against a recorded PAPER execution trace (literal values captured from the
paper executor for the same artifact/window, timestamps excluded).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.parity_harness import ParityMismatchError, check_parity
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.services.condition_compiler import ORDER_FILLED
from src.services.oms.contracts.v1_events import FillEvent

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_QTY = Decimal("9.090909090909090909090909091")


@dataclass
class _PriceResult:
    values: list[float | None]


class _PriceIndicatorService:
    """Serves the "PRICE" key (last close of the window) without TA-Lib."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> _PriceResult:
        return _PriceResult(values=[float(candles[-1].close)])


def _bar(index: int, open_price: str, close_price: str) -> Candle:
    ts = _T0 + timedelta(hours=index)
    o, c = Decimal(open_price), Decimal(close_price)
    return Candle(
        symbol="BTC/USDT", exchange="bitget", timeframe="1h", open=o, high=max(o, c),
        low=min(o, c), close=c, volume=Decimal("1"), open_time=ts, close_time=ts,
    )  # fmt: skip


def _transition(src: FSMState, dst: FSMState, cond: str) -> FSMTransition:
    return FSMTransition(from_state=src, to_state=dst, condition=cond)


def _replay() -> list:
    fsm = FSMStrategyConfig(
        strategy_id="parity", version="v1", target_asset="BTC/USDT", market="crypto",
        exchange="bitget", initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING,
                FSMState.SELL_ORDER_PENDING],
        transitions=[
            _transition(FSMState.IDLE, FSMState.BUY_ORDER_PENDING, "PRICE > 105"),
            _transition(FSMState.BUY_ORDER_PENDING, FSMState.HOLDING, ORDER_FILLED),
            _transition(FSMState.HOLDING, FSMState.SELL_ORDER_PENDING, "PRICE < 95"),
            _transition(FSMState.SELL_ORDER_PENDING, FSMState.IDLE, ORDER_FILLED),
        ],
        author_agent="test",
    )  # fmt: skip
    config = BacktestConfig(
        strategy_id="parity", strategy_version="v1", initial_equity=Decimal("1000"),
        cost_model=CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        warmup_bars=0, periods_per_year=252,
    )  # fmt: skip
    bars = [
        _bar(0, "100", "100"), _bar(1, "100", "110"), _bar(2, "112", "111"),
        _bar(3, "111", "90"), _bar(4, "88", "89"),
    ]  # fmt: skip
    return run_backtest(config, fsm, bars, indicator_service=_PriceIndicatorService()).fills


def _paper(seq: int, side: OrderSide, price: str) -> FillEvent:
    return FillEvent(
        provider_fill_id=f"paper-{seq}", venue="paper", order_id=uuid4(),
        exchange_order_id=f"paper-order-{seq}", symbol="BTC/USDT", side=side,
        quantity=_QTY, price=Decimal(price), fee=Decimal("0"), fee_currency="USDT",
        liquidity="TAKER", venue_ts=_T0 + timedelta(minutes=seq),
    )  # fmt: skip


_RECORDED_PAPER_TRACE = [_paper(0, OrderSide.BUY, "112"), _paper(1, OrderSide.SELL, "88")]


def test_real_replay_matches_recorded_paper_trace() -> None:
    report = check_parity(_RECORDED_PAPER_TRACE, _replay())
    report.raise_if_mismatch()
    assert report.paper_fill_count == report.backtest_fill_count == 2


def test_negative_one_tick_drift_in_paper_trace_is_detected() -> None:
    drifted = [
        _RECORDED_PAPER_TRACE[0],
        _RECORDED_PAPER_TRACE[1].model_copy(update={"price": Decimal("88.01")}),
    ]
    report = check_parity(drifted, _replay())
    with pytest.raises(ParityMismatchError, match="88"):
        report.raise_if_mismatch()


def test_negative_missing_paper_fill_is_detected() -> None:
    report = check_parity(_RECORDED_PAPER_TRACE[:1], _replay())
    with pytest.raises(ParityMismatchError):
        report.raise_if_mismatch()
