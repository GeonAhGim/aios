"""BT-10 — 즉시 백테스트(컬럼 경로 + BT-2~8 체결 모델 조립).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5(`application/quick_backtest.py` 차트 범위 즉시 백테스트, 컬럼 경로, 상한
봉수), §3.4, §7(1개월 M1 ≤5s), §9.5 BT-10,
docs/design/ADR-2026-09-04-A-market-data-replay-perf.md #1·#3.

체결 모델을 하나도 재구현하지 않는다 — 주문 1건의 생애주기(BT-2~8 위임)는
`quick_backtest_fill.py`에 있고, 이 파일은 봉 순회·전략 접점·포지션/현금
갱신·결과 조립만 한다.

캔들 입력은 LA-23b 컬럼지향 `CandleColumns`(ADR-A #1) 그대로다 — 레코드별
pydantic 재구성 없이 인덱스로 순회한다. 이 모듈은 I/O를 하지 않는다
(TID251: backtest/application은 asyncpg 금지). 호출자(BT-13 라우터·BT-11 잡)가
`columns = await store.read_candles_columnar(conn, key, start, end, as_of)`
한 번(왕복 1회)으로 읽어 넘긴다 — 통합테스트가 그 경로를 그대로 증명한다.

v1(`run_backtest.py`, `BacktestConfig`)은 건드리지 않고 v2(`BacktestConfigV2`,
BT-1) 경로로 병존한다(107번 §3.3).

결정론: 전역 시계·난수·dict/set 순회가 없고 모든 금액은 `Decimal`이다 —
같은 (config, columns, 전략) 입력이면 체결 로그가 바이트 동일하다.

미래 참조 금지(fail-closed): 전략은 현재 봉까지만 보이는 읽기 전용 뷰
`BarWindow`를 받는다 — 그 뒤 인덱스 접근은 `LookAheadError`. 신호는 봉 j의
open_time에 제출된 것으로 보고 BT-4(엄격 초과)가 체결 봉을 고르므로 체결
봉은 항상 j보다 뒤다(`domain/rules.is_look_ahead_safe`와 같은 원칙).

범위 밖(BT-11/12): OCO·트레일링, 체크포인트·진행률, 성과 지표(tearsheet).
실행 시간 상한은 결정론을 깨는 시계 접근이 필요하므로 호출자(async
timeout)의 책임이고, 여기서는 봉 수 상한(`MAX_QUICK_BARS`)만 건다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest_fill import (
    FillEvent,
    Holding,
    OrderIntent,
    PendingOrder,
    QuickBacktestInputError,
    price_path,
    settle_costs,
    submit_order,
    try_fill,
)
from src.foundation.backtest.domain.magnifier import validate_magnifier_config
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration

__all__ = [
    "MAX_QUICK_BARS",
    "BarWindow",
    "FillEvent",
    "LookAheadError",
    "OrderIntent",
    "PositionState",
    "QuickBacktestInputError",
    "QuickBacktestResult",
    "SignalSource",
    "TooManyBarsError",
    "run_quick_backtest",
]

MAX_QUICK_BARS = 44_640  # 31일 × 1,440 — 1개월 M1 상한(§2.5 "상한 봉수", §7 1개월 기준)
_ZERO = Decimal("0")


class TooManyBarsError(QuickBacktestInputError):
    """`BT_QUICK_TOO_MANY_BARS` — 즉시 백테스트 봉 수 상한 초과(BT-11 딥 백테스트 대상)."""


class LookAheadError(IndexError):
    """`BT_QUICK_LOOK_AHEAD` — 전략이 현재 봉보다 뒤의 봉을 읽으려 했다."""


@dataclass(frozen=True, slots=True)
class PositionState:
    quantity: Decimal
    cash: Decimal
    has_pending_order: bool


class BarWindow:
    """`columns[0:end)`만 보이는 읽기 전용 뷰(복사 없음). 음수 인덱스는 창 끝 기준."""

    __slots__ = ("_columns", "_end")

    def __init__(self, columns: CandleColumns, end_exclusive: int) -> None:
        self._columns = columns
        self._end = end_exclusive

    def __len__(self) -> int:
        return self._end

    def _index(self, i: int) -> int:
        j = i + self._end if i < 0 else i
        if not 0 <= j < self._end:
            raise LookAheadError(f"index {i}는 현재 창(길이 {self._end}) 밖이다 — 미래 참조 금지")
        return j

    def ts(self, i: int) -> datetime:
        return self._columns.ts[self._index(i)]

    def open(self, i: int) -> Decimal:
        return self._columns.open[self._index(i)]

    def high(self, i: int) -> Decimal:
        return self._columns.high[self._index(i)]

    def low(self, i: int) -> Decimal:
        return self._columns.low[self._index(i)]

    def close(self, i: int) -> Decimal:
        return self._columns.close[self._index(i)]

    def volume(self, i: int) -> Decimal:
        return self._columns.volume[self._index(i)]


class SignalSource(Protocol):
    """전략 접점. DSL-11 파사드가 컴파일 산출물을 이 형태로 감싼다(I-05) —
    이 모듈은 전략 내부를 모른다."""

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None: ...


@dataclass(frozen=True, slots=True)
class QuickBacktestResult:
    fills: tuple[FillEvent, ...]
    equity_curve: tuple[Decimal, ...]
    final_equity: Decimal
    cash: Decimal
    position_quantity: Decimal
    funding_cost: Decimal
    borrow_cost: Decimal
    bars: int
    expired_orders: int  # 데이터 범위 안에 체결 가능 봉이 없어 버려진 주문 수
    warnings: tuple[str, ...]


def _validate(
    config: BacktestConfigV2, columns: CandleColumns, timeframe: Timeframe,
    initial_cash: Decimal, funding_rate: Decimal | None, max_bars: int,
) -> None:
    n = len(columns)
    if n == 0:
        raise QuickBacktestInputError("캔들이 0개다 — 백테스트할 구간이 없다")
    if n > max_bars:
        raise TooManyBarsError(f"봉 수 {n}이 즉시 백테스트 상한 {max_bars}을 넘는다 — BT-11 대상")
    arrays = (columns.open, columns.high, columns.low, columns.close, columns.volume)
    if any(len(a) != n for a in arrays):
        raise QuickBacktestInputError("CandleColumns 배열 길이가 서로 다르다")
    if initial_cash.is_nan() or initial_cash < 0:
        raise QuickBacktestInputError(f"initial_cash는 음수·NaN을 허용하지 않는다: {initial_cash}")
    if config.costs.funding and funding_rate is None:
        raise QuickBacktestInputError(
            "costs.funding=True면 funding_rate가 필요하다 — 0으로 조용히 채우지 않는다"
        )
    validate_magnifier_config(higher_tf=timeframe, magnifier_tf=config.magnifier_tf)


def run_quick_backtest(
    config: BacktestConfigV2,
    columns: CandleColumns,
    *,
    timeframe: Timeframe,
    strategy: SignalSource,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
    max_bars: int = MAX_QUICK_BARS,
) -> QuickBacktestResult:
    """`columns`(LA-23b 컬럼 경로, `timeframe` 봉) 위에서 `strategy`를 봉마다
    한 번씩 평가하고 BT-2~8로 체결·비용을 계산한다. 대기 주문은 한 번에
    하나 — 새 의도가 오면 기존 대기 주문을 대체(취소)한다."""
    _validate(config, columns, timeframe, initial_cash, funding_rate, max_bars)
    n = len(columns)
    step = duration(timeframe)
    warnings: list[str] = []
    if config.magnifier_tf is not None and lower_columns is None:
        warnings.append(
            "magnifier_tf가 설정됐지만 lower_columns가 없어 봉 단위 확대로 대체했다(BT-7 (4))"
        )

    cash, qty = initial_cash, _ZERO
    pending: PendingOrder | None = None
    holding: Holding | None = None
    funding_total = borrow_total = _ZERO
    fills: list[FillEvent] = []
    equity: list[Decimal] = []
    expired = 0
    lower_cursor = 0

    for i in range(n):
        if pending is not None and i >= pending.execution_index:
            path, lower_cursor = price_path(
                config, columns, i, timeframe=timeframe, lower_columns=lower_columns,
                lower_cursor=lower_cursor,
            )
            fill = try_fill(config, pending, i, columns, path)
            if fill is not None:
                fills.append(fill)
                signed = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
                cash -= fill.price * signed + fill.commission
                before, qty = qty, qty + signed
                if holding is not None and (qty == 0 or (before > 0) != (qty > 0)):
                    f_cost, b_cost = settle_costs(config, holding, fill.open_time, funding_rate)
                    funding_total, borrow_total = funding_total + f_cost, borrow_total + b_cost
                    cash -= f_cost + b_cost
                    holding = None
                if holding is None and qty != 0:
                    holding = Holding(
                        opened_at=fill.open_time,
                        side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
                        notional=abs(qty) * fill.price,
                    )
                pending.remaining = fill.remaining_quantity
                if pending.remaining == 0:
                    pending = None

        equity.append(cash + qty * columns.close[i])

        position = PositionState(quantity=qty, cash=cash, has_pending_order=pending is not None)
        intent = strategy.on_bar(BarWindow(columns, i + 1), position)
        if intent is not None:
            submitted = submit_order(config, intent, columns, i, timeframe=timeframe)
            if submitted is None:
                expired += 1
            else:
                pending = submitted

    if holding is not None:
        f_cost, b_cost = settle_costs(config, holding, columns.ts[n - 1] + step, funding_rate)
        funding_total, borrow_total = funding_total + f_cost, borrow_total + b_cost
        cash -= f_cost + b_cost
    if pending is not None:
        expired += 1
    return QuickBacktestResult(
        fills=tuple(fills), equity_curve=tuple(equity),
        final_equity=cash + qty * columns.close[n - 1], cash=cash, position_quantity=qty,
        funding_cost=funding_total, borrow_cost=borrow_total, bars=n, expired_orders=expired,
        warnings=tuple(warnings),
    )
