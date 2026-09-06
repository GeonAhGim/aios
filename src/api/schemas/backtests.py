"""BT-10c — `POST /v1/backtests/quick` 요청·응답 스키마.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.5 BT-10,
§3.4(`BacktestConfigV2`).

식별자 규칙은 LA-24(`src/api/schemas/market_data.py`/`application/read_api.
resolve_instrument`)와 같다 — `instrument_id`가 있으면 우선, 없으면
`venue`+`symbol`. `script_source`의 길이 상한은 DSL-12와 같은 값
(`scripts.MAX_SOURCE_CHARS`)을 재사용한다 — 트랜스포트 상한을 두 곳에서
따로 정의하지 않는다.

`initial_cash`/`funding_rate`는 `run_quick_backtest`(BT-10)의 필수·선택
인자를 그대로 옮긴 것이다 — `BacktestConfigV2`(BT-1)에는 없는 값이라
요청에 별도로 싣는다.

응답은 `QuickBacktestResult`(BT-10)를 그대로 뷰로 옮긴다. `Decimal` 필드는
pydantic v2 JSON 모드 기본 동작(문자열 직렬화)을 그대로 쓴다 — 별도
인코더가 필요 없다. `fills`는 원본 튜플 순서(체결 시각 순, 결정론)를
그대로 유지하고 재정렬하지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from src.api.schemas.scripts import MAX_SOURCE_CHARS
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.application.quick_backtest_fill import FillEvent, OrderType
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.contracts.v1 import Timeframe, Venue

__all__ = [
    "FillView",
    "QuickBacktestRequest",
    "QuickBacktestResultView",
]


class QuickBacktestRequest(BaseModel):
    venue: Venue
    symbol: str | None = None
    instrument_id: UUID | None = None
    timeframe: Timeframe
    start: AwareDatetime
    end: AwareDatetime
    as_of: AwareDatetime | None = None
    initial_cash: Decimal = Field(gt=0)
    funding_rate: Decimal | None = None
    config: BacktestConfigV2
    script_source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)


class FillView(BaseModel):
    bar_index: int
    open_time: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal
    commission: Decimal
    remaining_quantity: Decimal

    @classmethod
    def from_fill(cls, fill: FillEvent) -> FillView:
        return cls(
            bar_index=fill.bar_index,
            open_time=fill.open_time,
            side=fill.side,
            order_type=fill.order_type,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            remaining_quantity=fill.remaining_quantity,
        )


class QuickBacktestResultView(BaseModel):
    fills: list[FillView]
    equity_curve: list[Decimal]
    final_equity: Decimal
    cash: Decimal
    position_quantity: Decimal
    funding_cost: Decimal
    borrow_cost: Decimal
    bars: int
    expired_orders: int
    warnings: list[str]

    @classmethod
    def from_result(cls, result: QuickBacktestResult) -> QuickBacktestResultView:
        return cls(
            fills=[FillView.from_fill(f) for f in result.fills],
            equity_curve=list(result.equity_curve),
            final_equity=result.final_equity,
            cash=result.cash,
            position_quantity=result.position_quantity,
            funding_cost=result.funding_cost,
            borrow_cost=result.borrow_cost,
            bars=result.bars,
            expired_orders=result.expired_orders,
            warnings=list(result.warnings),
        )
