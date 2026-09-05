"""BT-10 — 즉시 백테스트의 주문 1건 생애주기(BT-2~8 조립, 재구현 없음).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.5 BT-10,
§3.4. `quick_backtest.py`(루프·전략 접점)에서 분리한 이유는 300줄 상한 —
이 파일의 단일 책임은 "대기 주문 하나가 어느 봉에서 얼마에 얼마나 체결되고
포지션 비용이 어떻게 정산되는가"이며, 봉 순회·전략 호출은 하지 않는다.

위임 표(각 함수가 호출하는 도메인 리프):
- `submit_order`   → BT-4 `latency.resolve_execution_bar_index`,
                     BT-6 `order_types.ensure_order_type_enabled`
- `price_path`     → BT-7 `magnifier.magnify`(하위 TF 슬라이스는 여기서 자른다)
- `try_fill`       → BT-6 `is_limit_triggered`/`is_stop_triggered`,
                     BT-5 `compute_partial_fill`(잔량 이월은 호출자),
                     BT-2 `apply_slippage`, BT-3 `compute_commission`
- `settle_costs`   → BT-8 `compute_funding_cost`/`compute_borrow_cost`

순수 함수만 — I/O·시계·난수 없음, 금액은 전부 `Decimal`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.costs.borrow import compute_borrow_cost
from src.foundation.backtest.domain.costs.funding import compute_funding_cost
from src.foundation.backtest.domain.fill.commission import compute_commission
from src.foundation.backtest.domain.fill.latency import resolve_execution_bar_index
from src.foundation.backtest.domain.fill.order_types import (
    ensure_order_type_enabled,
    is_limit_triggered,
    is_stop_triggered,
)
from src.foundation.backtest.domain.fill.partial_fill import compute_partial_fill
from src.foundation.backtest.domain.fill.slippage import apply_slippage
from src.foundation.backtest.domain.magnifier import HigherBar, magnify
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration

OrderType = Literal["market", "limit", "stop"]
_ZERO = Decimal("0")


class QuickBacktestInputError(ValueError):
    """`BT_QUICK_INPUT` — 입력 자체가 계약을 만족하지 못한다(fail-closed)."""


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """전략이 봉 하나에서 내는 주문 의도. `limit`/`stop`은 `trigger_price` 필수.
    OCO·트레일링은 즉시 백테스트 범위 밖(BT-11)이라 표현할 수 없다."""

    side: OrderSide
    quantity: Decimal
    order_type: OrderType = "market"
    trigger_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FillEvent:
    bar_index: int
    open_time: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal
    commission: Decimal
    remaining_quantity: Decimal  # 부분체결(BT-5) 이월 잔량 — 0이면 완전 체결


@dataclass(slots=True)
class PendingOrder:
    intent: OrderIntent
    remaining: Decimal
    execution_index: int  # BT-4가 정한 첫 체결 가능 봉


@dataclass(frozen=True, slots=True)
class Holding:
    """비용 정산(BT-8) 단위 — 포지션이 0에서 벗어난 시점부터 0으로 돌아올 때까지."""

    opened_at: datetime
    side: OrderSide
    notional: Decimal


def submit_order(
    config: BacktestConfigV2, intent: OrderIntent, columns: CandleColumns, signal_index: int,
    *, timeframe: Timeframe,
) -> PendingOrder | None:
    """봉 `signal_index`의 open_time에 제출된 주문의 첫 체결 가능 봉을 BT-4로
    정한다. 데이터 범위 안에 그런 봉이 없으면(갭·말단) `None`(만료)."""
    if intent.quantity.is_nan() or intent.quantity <= 0:
        raise QuickBacktestInputError(f"주문 수량은 양수여야 한다: {intent.quantity}")
    if intent.order_type != "market":
        ensure_order_type_enabled(config.order_types, intent.order_type)
        if intent.trigger_price is None:
            raise QuickBacktestInputError(f"{intent.order_type} 주문은 trigger_price가 필요하다")
    step_ms = int(duration(timeframe).total_seconds() * 1000)
    tail_start = signal_index + 1
    span = config.latency_ms // step_ms + 2  # 지연이 덮는 봉 수 + 여유 — 슬라이스 복사 상한
    short_tail = columns.ts[tail_start : tail_start + span]
    offset = _resolve_offset(config, columns, signal_index, short_tail)
    if offset is None and tail_start + span < len(columns):
        # 짧은 창 안에 없을 때만(갭) 전체 꼬리를 복사한다 — 주문마다 O(n) 복사를 피한다.
        offset = _resolve_offset(config, columns, signal_index, columns.ts[tail_start:])
    if offset is None:
        return None
    return PendingOrder(
        intent=intent, remaining=intent.quantity, execution_index=tail_start + offset
    )


def _resolve_offset(
    config: BacktestConfigV2, columns: CandleColumns, signal_index: int, tail: list[datetime]
) -> int | None:
    try:
        return resolve_execution_bar_index(
            submitted_at=columns.ts[signal_index], latency_ms=config.latency_ms, bar_open_times=tail
        )
    except LookupError:
        return None


def _slice_lower(
    lower: CandleColumns, cursor: int, start: datetime, end: datetime
) -> tuple[CandleColumns, int]:
    while cursor < len(lower) and lower.ts[cursor] < start:
        cursor += 1
    stop = cursor
    while stop < len(lower) and lower.ts[stop] < end:
        stop += 1
    sliced = CandleColumns(
        ts=lower.ts[cursor:stop], open=lower.open[cursor:stop], high=lower.high[cursor:stop],
        low=lower.low[cursor:stop], close=lower.close[cursor:stop],
        volume=lower.volume[cursor:stop], quote_volume=lower.quote_volume[cursor:stop],
    )
    return sliced, stop


def price_path(
    config: BacktestConfigV2, columns: CandleColumns, i: int, *, timeframe: Timeframe,
    lower_columns: CandleColumns | None, lower_cursor: int,
) -> tuple[tuple[Decimal, ...], int]:
    """봉 `i`의 가격 방문 순서(BT-7)와 전진한 하위 봉 커서. 하위 봉은 시간순
    정렬 전제(어댑터 ORDER BY)로 커서를 한 방향으로만 옮겨 전체 O(n)."""
    bar = HigherBar(
        open_time=columns.ts[i], open=columns.open[i], high=columns.high[i],
        low=columns.low[i], close=columns.close[i],
    )
    lower_slice = None
    if config.magnifier_tf is not None and lower_columns is not None:
        lower_slice, lower_cursor = _slice_lower(
            lower_columns, lower_cursor, bar.open_time, bar.open_time + duration(timeframe)
        )
    path = magnify(
        bar, higher_tf=timeframe, magnifier_tf=config.magnifier_tf, lower_bars=lower_slice
    )
    return path, lower_cursor


def _triggered(intent: OrderIntent, trigger: Decimal, lo: Decimal, hi: Decimal) -> bool:
    if intent.order_type == "limit":
        return is_limit_triggered(side=intent.side, limit_price=trigger, bar_low=lo, bar_high=hi)
    return is_stop_triggered(side=intent.side, stop_price=trigger, bar_low=lo, bar_high=hi)


def _reference_price(intent: OrderIntent, path: tuple[Decimal, ...]) -> Decimal | None:
    """가격 방문 순서를 세그먼트로 훑어 BT-6 트리거가 처음 닿는 지점의 기준가.
    세그먼트 시작가가 이미 트리거를 넘겼으면(갭) 그 시작가로 체결한다."""
    if intent.order_type == "market":
        return path[0]
    trigger = intent.trigger_price
    if trigger is None:  # submit_order가 이미 거부한 경로 — 방어적 fail-closed
        raise QuickBacktestInputError(f"{intent.order_type} 주문은 trigger_price가 필요하다")
    prev = path[0]
    for point in path:
        if _triggered(intent, trigger, min(prev, point), max(prev, point)):
            return prev if _triggered(intent, trigger, prev, prev) else trigger
        prev = point
    return None


def try_fill(
    config: BacktestConfigV2, pending: PendingOrder, i: int, columns: CandleColumns,
    path: tuple[Decimal, ...],
) -> FillEvent | None:
    """봉 `i`에서 대기 주문을 체결 시도한다. 트리거 미달·거래량 0이면 `None`
    (호출자가 다음 봉으로 이월)."""
    intent = pending.intent
    reference = _reference_price(intent, path)
    if reference is None:
        return None
    volume = columns.volume[i]
    outcome = compute_partial_fill(
        config.partial_fill, order_quantity=pending.remaining, bar_volume=volume
    )
    if outcome.filled_quantity == 0:
        return None
    price = apply_slippage(
        config.slippage, side=intent.side, reference_price=reference,
        quantity=outcome.filled_quantity, bar_volume=volume,
    )
    commission = compute_commission(
        config.commission, is_maker=intent.order_type == "limit",
        notional=price * outcome.filled_quantity,
    )
    return FillEvent(
        bar_index=i, open_time=columns.ts[i], side=intent.side, order_type=intent.order_type,
        quantity=outcome.filled_quantity, price=price, commission=commission,
        remaining_quantity=outcome.remaining_quantity,
    )


def settle_costs(
    config: BacktestConfigV2, holding: Holding, exit_time: datetime, funding_rate: Decimal | None
) -> tuple[Decimal, Decimal]:
    """(펀딩, 차입) 비용. `funding=False`면 BT-8이 rate를 보지 않고 0을 돌려주고,
    `True`면 호출자(`run_quick_backtest`)가 rate 존재를 미리 강제했다."""
    rate = funding_rate if funding_rate is not None else _ZERO
    funding = compute_funding_cost(
        config.costs, side=holding.side, notional=holding.notional, funding_rate=rate,
        entry_time=holding.opened_at, exit_time=exit_time,
    )
    borrow = _ZERO
    if holding.side == OrderSide.SELL:  # 차입은 공매도(숏) 포지션에만
        borrow = compute_borrow_cost(
            config.costs, notional=holding.notional, entry_time=holding.opened_at,
            exit_time=exit_time,
        )
    return funding, borrow
