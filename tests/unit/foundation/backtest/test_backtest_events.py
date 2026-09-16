"""L25 -- BarEvent/SignalEvent/OrderEvent/FillEvent 불변식 + 실패 주입
(D2 하한, ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L25.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.models import Signal
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.events import (
    BarEvent,
    FillEvent,
    OrderEvent,
    SignalEvent,
    append_event,
)
from src.foundation.backtest.domain.models import SimulatedFill
from src.foundation.backtest.domain.rules import (
    LookaheadViolationError,
    assert_fill_after_signal,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
        open_time=ts,
        close_time=ts + timedelta(hours=1),
    )


def _signal() -> Signal:
    return Signal(
        strategy_id="s1",
        strategy_version="v1",
        symbol="BTC/USDT",
        direction=OrderSide.BUY,
        confidence=0.9,
        target_position=Decimal("0"),
        stop_loss=None,
        take_profit=None,
        timestamp=_T0,
        to_state=FSMState.BUY_ORDER_PENDING,
    )


def _fill() -> SimulatedFill:
    return SimulatedFill(
        bar_index=1,
        timestamp=_T0,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        slippage_cost=Decimal("0.05"),
    )


def test_order_event_is_frozen() -> None:
    ev = OrderEvent(
        sequence_no=0,
        occurred_at=_T0,
        bar_index=0,
        side=OrderSide.BUY,
        qty=Decimal("1"),
        decision_hash="a" * 64,
    )
    with pytest.raises(ValidationError):
        ev.__setattr__("qty", Decimal("2"))


def test_bar_signal_fill_events_roundtrip() -> None:
    BarEvent(sequence_no=0, occurred_at=_T0, bar_index=0, tf="1h", bar=_bar(0))
    SignalEvent(sequence_no=1, occurred_at=_T0, bar_index=0, signal=_signal())
    FillEvent(sequence_no=2, occurred_at=_T0, bar_index=1, fill=_fill())


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        OrderEvent(
            sequence_no=0,
            occurred_at=datetime(2026, 1, 1),  # naive
            bar_index=0,
            side=OrderSide.BUY,
            qty=Decimal("1"),
            decision_hash="a" * 64,
        )


def test_append_event_accepts_strictly_increasing_sequence() -> None:
    ev0 = OrderEvent(
        sequence_no=0,
        occurred_at=_T0,
        bar_index=0,
        side=OrderSide.BUY,
        qty=Decimal("1"),
        decision_hash="a" * 64,
    )
    ev1 = FillEvent(sequence_no=1, occurred_at=_T0, bar_index=1, fill=_fill())
    events = append_event((), ev0)
    events = append_event(events, ev1)
    assert events == (ev0, ev1)


def test_append_event_rejects_non_increasing_sequence() -> None:
    ev0 = OrderEvent(
        sequence_no=5,
        occurred_at=_T0,
        bar_index=0,
        side=OrderSide.BUY,
        qty=Decimal("1"),
        decision_hash="a" * 64,
    )
    ev_regress = FillEvent(sequence_no=5, occurred_at=_T0, bar_index=1, fill=_fill())
    events = append_event((), ev0)
    with pytest.raises(ValueError, match="단조 증가"):
        append_event(events, ev_regress)


# --------------------------------------------------------------------------
# D2 실패 주입 -- rules.assert_fill_after_signal이 실제 OrderEvent/FillEvent에
# 배선돼 있음을 증명
# --------------------------------------------------------------------------


def test_assert_fill_after_signal_rejects_lookahead_using_real_domain_events() -> None:
    """이 모듈 docstring이 선언한 순서 불변식(§2.4 domain/events.py 행: 같은
    bar_index 안에서 `Fill(previous Order) < Bar < Signal < Order`)을 실제로
    집행하는 곳은 `rules.py`의 `assert_fill_after_signal`이다 -- `_HasBarIndex`
    프로토콜은 정확히 `OrderEvent`/`FillEvent`가 만족하도록 설계돼 있다(rules.py
    주석 참고). 리플레이 루프에 체결 시각 계산 회귀가 생겨 주문과 같은 bar에
    체결된 `FillEvent`를 만들어내면(look-ahead bias), 두 모듈이 실제로 배선돼
    있는 한 `LookaheadViolationError`가 조용히 삼켜지지 않고 전파돼야 한다."""
    order_ev = OrderEvent(
        sequence_no=0,
        occurred_at=_T0,
        bar_index=5,
        side=OrderSide.BUY,
        qty=Decimal("1"),
        decision_hash="a" * 64,
    )
    regressed_fill_ev = FillEvent(sequence_no=1, occurred_at=_T0, bar_index=5, fill=_fill())
    with pytest.raises(LookaheadViolationError):
        assert_fill_after_signal(order_ev, regressed_fill_ev)
