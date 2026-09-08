"""BT-15b (2/2) — vector-signal 배열을 BT-2~6 이벤트 체결 엔진에 연결.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(2/2). 선행: BT-15a `vector/{arrays,signals}.py`(13e16cc2), BT-2~6 체결
현실성(fa3afe4), `quick_backtest`(9a1ae87).

체결은 대기 주문 잔량·포지션·펀딩/차입 정산이 봉을 가로질러 이월되는 상태
기계다(`quick_backtest_fill.PendingOrder`/`Holding`) — 원소마다 독립적인
numpy 배열 연산으로 병렬화할 수 없다. 그래서 이 모듈은 체결 산식을 배열로
다시 쓰지 않는다(§C 중복 컨텍스트·I-05 위반 회피): BT-15a `signals.py`가
봉 전체를 한 번에 벡터 연산해 낸 진입/청산 `BoolSignal`("벡터"는 여기까지)을
BT-10 `quick_backtest.run_quick_backtest`가 요구하는 `SignalSource` 프로토콜
어댑터로 감싸, BT-2~6을 그대로 실행하는 기존 이벤트 루프에 그대로 넘긴다.
`arrays.py` 모듈 docstring이 이미 선언한 설계(신호=float64 벡터, 체결=
Decimal 이벤트 엔진)를 그대로 따른다 — 신뢰 가능한 체결 로그는 항상 이
이벤트 경로가 낸다. 이 설계 덕분에 벡터 경로와 이벤트 경로의 체결·equity는
근사가 아니라 항상 정확히 같다(같은 함수 호출이므로).

롱 온리(숏 신호는 이 리프 범위 밖 — 필요해지면 별도 리프에서 명시적으로
다룬다, 추측으로 지금 만들지 않는다).

순수 모듈 — I/O 없음.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
    QuickBacktestResult,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["VectorFillsError", "VectorSignal", "run_vector_backtest"]


class VectorFillsError(ValueError):
    """`BT_VECTOR_FILLS` — signal 길이 불일치·수량 계약 위반 fail-closed 거부."""


@dataclass(frozen=True, slots=True)
class VectorSignal:
    """BT-15a가 봉 전체에 대해 한 번에 계산한 진입/청산 불리언 배열.
    `entries[i]`가 확정 참(na 아님)이고 포지션이 0이면 봉 `i`에서 시장가
    매수, `exits[i]`가 확정 참이고 포지션이 있으면 시장가 전량 청산한다."""

    entries: BoolSignal
    exits: BoolSignal
    quantity: Decimal

    def __post_init__(self) -> None:
        if len(self.entries) != len(self.exits):
            raise VectorFillsError(
                f"entries 길이 {len(self.entries)} != exits 길이 {len(self.exits)}"
            )
        if self.quantity.is_nan() or self.quantity <= 0:
            raise VectorFillsError(f"quantity는 양수여야 한다: {self.quantity}")


def _is_true(signal: BoolSignal, i: int) -> bool:
    return not signal.na[i] and bool(signal.values[i])


class _VectorSignalSource:
    """`VectorSignal` 배열 조회만 하는 `SignalSource`(BT-10 프로토콜) 어댑터.
    `window`의 마지막 인덱스(`len(window) - 1` = 현재 봉)만 읽는다 — 그 이상
    (t+1 이후)은 `BarWindow`가 이미 `LookAheadError`로 막으므로 이 어댑터는
    아예 접근할 수 없다."""

    __slots__ = ("_signal",)

    def __init__(self, signal: VectorSignal) -> None:
        self._signal = signal

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        i = len(window) - 1
        signal = self._signal
        if position.quantity == 0 and _is_true(signal.entries, i):
            return OrderIntent(side=OrderSide.BUY, quantity=signal.quantity)
        if position.quantity > 0 and _is_true(signal.exits, i):
            return OrderIntent(side=OrderSide.SELL, quantity=position.quantity)
        return None


def run_vector_backtest(
    config: BacktestConfigV2,
    columns: CandleColumns,
    signal: VectorSignal,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
) -> QuickBacktestResult:
    """`signal`(BT-15a 벡터 경로가 낸 진입/청산 배열)을 `columns` 위에서
    재생한다. 체결은 전부 BT-10 이벤트 엔진(BT-2~6 위임)이 낸다 — 이 함수는
    신호 조회 방식만 다르다(DSL 전략 콜백 대신 사전 계산된 배열)."""
    if len(signal.entries) != len(columns):
        raise VectorFillsError(
            f"signal 길이 {len(signal.entries)} != 캔들 수 {len(columns)}"
        )
    return run_quick_backtest(
        config, columns, timeframe=timeframe, strategy=_VectorSignalSource(signal),
        initial_cash=initial_cash, funding_rate=funding_rate, lower_columns=lower_columns,
    )
