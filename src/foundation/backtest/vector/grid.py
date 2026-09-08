"""BT-16a (1/3) — `backtest/vector/grid.py`: 파라미터 조합 대량 실행.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. 원본 BT-16 리프는 BT-15·AI-10(실험 원장) 선행이지만 AI-10이
미착수라 task-2371 decision이 리프를 둘로 쪼갰다 — 이 모듈(grid.py)과
형제 파일 `walk_forward.py`/`monte_carlo.py`는 대량 실행부만 담당하고,
조합별 결과를 실험 원장에 영속화하는 일(BT-16b)은 AI-10 이후로 미룬다.
이 모듈은 원장에 아무것도 쓰지 않는다 — 반환값을 어디에 남길지는 호출자
책임이다.

BT-17 `universe.py`가 종목마다 같은 `config`로 스윕했던 것과 대칭이다:
이 모듈은 파라미터 조합마다 다른 `VectorSignal`(신호 자체를 만드는 일은
DSL/전략 계층 책임 — 이 리프는 신호를 만들지 않고 받기만 한다) 을 같은
`CandleColumns` 위에서 스윕한다. 체결 산식은 다시 구현하지 않는다(I-05,
§C 중복 컨텍스트 회피) — BT-15b `run_vector_backtest`(BT-2~6 이벤트 체결
엔진 위임)를 조합마다 그대로 호출한다.

순수 모듈 — I/O 없음.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["GridSweepResult", "sweep_grid"]


@dataclass(frozen=True, slots=True)
class GridSweepResult:
    """`results`는 호출자가 붙인 조합 키(예: `"rsi_len=14,exit=20"`) ->
    그 조합의 `QuickBacktestResult`. 키 집합은 항상 입력 `combos` 키 집합과
    정확히 같다(조용히 누락되는 조합이 없다) — 조합 하나라도 실패하면
    `run_vector_backtest`가 던지는 예외가 그대로 전파되어 부분 결과를
    만들지 않는다."""

    results: dict[str, QuickBacktestResult]


def sweep_grid(
    columns: CandleColumns,
    combos: Mapping[str, VectorSignal],
    config: BacktestConfigV2,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> GridSweepResult:
    """`combos`의 각 조합(같은 `columns` 위에서 신호만 다름)에 같은 `config`로
    `run_vector_backtest`를 돌린다. `combos`가 비어 있으면 빈 결과를 낸다
    (`universe.sweep_universe`가 빈 유니버스를 허용하는 것과 같은 선례)."""
    results = {
        key: run_vector_backtest(
            config, columns, signal, timeframe=timeframe,
            initial_cash=initial_cash, funding_rate=funding_rate,
        )
        for key, signal in combos.items()
    }
    return GridSweepResult(results=results)
