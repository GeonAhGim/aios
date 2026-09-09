"""L25 -- BarEvent/SignalEvent/OrderEvent/FillEvent: the immutable event log
that event-driven replay (L30 `application/event_loop.py`) records.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§2.4
(`domain/events.py` row -- ordering invariant: within the same bar_index,
`Fill(previous Order) < Bar < Signal < Order`).

Every event carries a `sequence_no` that is monotonically increasing across
the whole replay log and a tz-aware `occurred_at` (a naive datetime could
mix up past and future, so it is rejected). `append_event` is the single
assembly point that enforces that ordering.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.core.strategy.models import Signal
from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import SimulatedFill


class _BacktestEventBase(BaseModel, frozen=True):
    sequence_no: int = Field(ge=0)
    occurred_at: datetime
    bar_index: int = Field(ge=0)

    @field_validator("occurred_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("naive datetime은 허용하지 않는다 -- tz-aware UTC만 사용한다")
        return value


class BarEvent(_BacktestEventBase, frozen=True):
    event_type: Literal["BAR"] = "BAR"
    tf: str
    bar: Candle


class SignalEvent(_BacktestEventBase, frozen=True):
    event_type: Literal["SIGNAL"] = "SIGNAL"
    signal: Signal


class OrderEvent(_BacktestEventBase, frozen=True):
    event_type: Literal["ORDER"] = "ORDER"
    side: OrderSide
    qty: Decimal
    decision_hash: str


class FillEvent(_BacktestEventBase, frozen=True):
    event_type: Literal["FILL"] = "FILL"
    fill: SimulatedFill


BacktestEvent = BarEvent | SignalEvent | OrderEvent | FillEvent


def append_event(
    events: tuple[BacktestEvent, ...], event: BacktestEvent
) -> tuple[BacktestEvent, ...]:
    """The single assembly point that enforces `sequence_no` monotonically
    increasing across the whole log -- a regressing input means the replay
    order broke, so it is rejected with an exception."""
    if events and event.sequence_no <= events[-1].sequence_no:
        raise ValueError(
            "sequence_no는 단조 증가해야 한다: "
            f"{event.sequence_no} <= {events[-1].sequence_no}"
        )
    return (*events, event)


__all__ = [
    "BacktestEvent",
    "BarEvent",
    "SignalEvent",
    "OrderEvent",
    "FillEvent",
    "append_event",
]
