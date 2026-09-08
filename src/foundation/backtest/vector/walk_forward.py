"""BT-16a (2/3) — `backtest/vector/walk_forward.py`: 롤링 훈련/검증 창 실행.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. task-2371 decision — `grid.py` 모듈 docstring과 같은 분할(AI-10 실험
원장 기록은 BT-16b로 미룸, 이 리프는 실행부만).

워크포워드는 데이터를 연속한 (훈련 구간, 검증 구간) 창으로 나눠, 각 창의
훈련 구간에서 가장 좋은 조합을 고른 뒤 그 조합을 같은 창의 검증 구간(훈련이
보지 못한 구간)에서만 채점한다 — 훈련 구간 성과로 조합을 고르는 로직 자체가
이 리프의 핵심이다("실행부"이지 최적화 알고리즘 리프가 아니다: 조합 후보와
그 신호는 여전히 호출자가 만들어 넘긴다). 선택 지표는
`QuickBacktestResult.final_equity`(다른 지표(샤프 등)는 이 리프 범위 밖 —
필요해지면 별도 리프에서 명시적으로 다룬다, 추측으로 지금 만들지 않는다).

체결 산식은 다시 구현하지 않는다(I-05) — `grid.py`처럼 매 창·매 조합마다
BT-15b `run_vector_backtest`를 그대로 호출한다. 창을 나누는 절단은
`CandleColumns`/`VectorSignal` 배열 슬라이스만 한다(BT-11
`deep_backtest_job._prefix` 선례와 같은 방식 — 새 시계열 표현을 신설하지
않는다).

순수 모듈 — I/O 없음.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "WalkForwardError",
    "WalkForwardResult",
    "WalkForwardWindow",
    "WalkForwardWindowSpec",
    "run_walk_forward",
]


class WalkForwardError(ValueError):
    """`BT_VECTOR_WALK_FORWARD` — 조합 없음·창 크기가 데이터보다 큼 등
    fail-closed 거부."""


@dataclass(frozen=True, slots=True)
class WalkForwardWindowSpec:
    """`train_bars`개로 조합을 고르고, 바로 이어지는 `test_bars`개로 채점한다.
    `step_bars`(생략 시 `test_bars`와 같음, 즉 검증 구간끼리 겹치지 않고
    이어 붙는 기본값)만큼 다음 창의 훈련 시작점을 민다."""

    train_bars: int
    test_bars: int
    step_bars: int | None = None

    def __post_init__(self) -> None:
        if self.train_bars <= 0:
            raise WalkForwardError(f"train_bars는 양수여야 한다: {self.train_bars}")
        if self.test_bars <= 0:
            raise WalkForwardError(f"test_bars는 양수여야 한다: {self.test_bars}")
        if self.step_bars is not None and self.step_bars <= 0:
            raise WalkForwardError(f"step_bars는 양수여야 한다: {self.step_bars}")

    @property
    def effective_step_bars(self) -> int:
        return self.step_bars if self.step_bars is not None else self.test_bars


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """`[train_start, train_end)`가 조합 선택에 쓰인 훈련 구간,
    `[test_start, test_end)`(항상 `test_start == train_end`)가 선택된
    `selected_combo`를 채점한 검증 구간이다."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int
    selected_combo: str
    train_result: QuickBacktestResult
    test_result: QuickBacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    windows: tuple[WalkForwardWindow, ...]


def run_walk_forward(
    columns: CandleColumns,
    combos: Mapping[str, VectorSignal],
    config: BacktestConfigV2,
    window: WalkForwardWindowSpec,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> WalkForwardResult:
    """`columns` 전체를 `window` 규격의 (훈련, 검증) 창으로 순차 분할하고,
    창마다 `combos`를 전부 훈련 구간에서 실행해 `final_equity`가 가장 큰
    조합을 골라 검증 구간에서 다시 실행한다.

    창을 하나도 만들 수 없으면(데이터가 `train_bars+test_bars`보다 짧으면)
    빈 결과를 조용히 반환하지 않고 거부한다 — "창 0개짜리 워크포워드"는
    호출자가 창 규격이나 데이터 범위를 잘못 짰다는 신호이지 정상적인
    빈 결과가 아니다."""
    if not combos:
        raise WalkForwardError("combos가 비어 있다 — 훈련 구간에서 고를 조합이 없다")

    n = len(columns)
    step = window.effective_step_bars
    windows: list[WalkForwardWindow] = []
    train_start = 0
    while True:
        train_end = train_start + window.train_bars
        test_end = train_end + window.test_bars
        if test_end > n:
            break

        train_columns = _slice_columns(columns, train_start, train_end)
        train_results = {
            key: run_vector_backtest(
                config, train_columns, _slice_signal(signal, train_start, train_end),
                timeframe=timeframe, initial_cash=initial_cash, funding_rate=funding_rate,
            )
            for key, signal in combos.items()
        }
        best_key = max(train_results, key=lambda k: train_results[k].final_equity)

        test_columns = _slice_columns(columns, train_end, test_end)
        test_result = run_vector_backtest(
            config, test_columns, _slice_signal(combos[best_key], train_end, test_end),
            timeframe=timeframe, initial_cash=initial_cash, funding_rate=funding_rate,
        )
        windows.append(WalkForwardWindow(
            train_start=train_start, train_end=train_end,
            test_start=train_end, test_end=test_end,
            selected_combo=best_key,
            train_result=train_results[best_key], test_result=test_result,
        ))
        train_start += step

    if not windows:
        raise WalkForwardError(
            f"창을 하나도 만들 수 없다 — train_bars+test_bars="
            f"{window.train_bars + window.test_bars}가 캔들 수 {n}보다 크다"
        )
    return WalkForwardResult(windows=tuple(windows))


def _slice_columns(columns: CandleColumns, start: int, end: int) -> CandleColumns:
    """BT-11 `deep_backtest_job._prefix`와 같은 필드별 수동 슬라이스 —
    `CandleColumns`는 슬라이스 헬퍼가 없는 순수 데이터 홀더 dataclass다."""
    return CandleColumns(
        ts=columns.ts[start:end], open=columns.open[start:end], high=columns.high[start:end],
        low=columns.low[start:end], close=columns.close[start:end],
        volume=columns.volume[start:end], quote_volume=columns.quote_volume[start:end],
    )


def _slice_signal(signal: VectorSignal, start: int, end: int) -> VectorSignal:
    return VectorSignal(
        entries=BoolSignal(
            values=signal.entries.values[start:end], na=signal.entries.na[start:end],
        ),
        exits=BoolSignal(
            values=signal.exits.values[start:end], na=signal.exits.na[start:end],
        ),
        quantity=signal.quantity,
    )
