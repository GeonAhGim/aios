"""BT-16a (3/3) — `backtest/vector/monte_carlo.py`: 봉별 수익률 재표본 몬테카를로.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. task-2371 decision — `grid.py` 모듈 docstring과 같은 분할(AI-10 실험
원장 기록은 BT-16b로 미룸, 이 리프는 실행부만).

`grid.py`/`walk_forward.py`는 조합·창마다 이벤트 엔진(`run_vector_backtest`)을
다시 호출해 새 체결을 만든다. 이 모듈은 다르다 — BT-15b가 이미 낸 단일
`QuickBacktestResult.equity_curve`(Decimal 튜플, 봉마다의 원장 자본)를
진실로 삼아 체결·비용을 다시 계산하지 않는다(I-05, 이벤트 엔진 재호출
없음). `equity_curve`에서 봉 사이 수익률을 뽑아 복원추출(bootstrap)로
순서를 섞은 경로를 `iterations`개 만들어 "이 전략의 최종 성과가 실현된 봉
순서에 얼마나 의존하는가"를 정량화한다 — 파라미터나 창을 바꾸는 게 아니라
같은 수익률 표본을 재배열만 하므로 이벤트 루프 재실행 비용이 없다(BT-16
DoD "1,000 조합 ≤60s"를 이 리프에서는 순수 numpy 벡터 연산으로 만족한다).

결정론: 난수는 `numpy.random.Generator`를 인자로 명시적으로 받는다 —
전역 난수 상태(`np.random.seed` 등)에 의존하면 같은 호출이 실행마다 다른
결과를 내 재현 키(BT-9)와 맞물릴 여지가 없어진다. 시드를 고정한 `Generator`를
넘기면 이 함수는 항상 같은 결과를 낸다.

`Decimal` 왕복(`Decimal(str(float))`)은 `arrays.py` 모듈 docstring이 이미
선언한 것과 같은 의도적 정밀도 손실이다 — 이 리프는 통계적 분포 요약이지
회계 정확도가 필요한 체결 로그가 아니다.

순수 모듈 — I/O 없음(난수는 인자로 주입되므로 숨은 부수효과가 아니다).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult

__all__ = ["MonteCarloError", "MonteCarloResult", "run_monte_carlo"]

FloatArray = np.ndarray[Any, np.dtype[np.float64]]

_DEFAULT_PERCENTILES = (5, 25, 50, 75, 95)


class MonteCarloError(ValueError):
    """`BT_VECTOR_MONTE_CARLO` — `iterations`<=0·`equity_curve` 길이 부족·
    0 자본 구간·백분위 범위 밖 등 fail-closed 거부."""


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """`final_equities[i]`는 `i`번째 재표본 경로의 최종 자본(입력 순서 =
    반복 순서, 정렬 안 됨). `percentiles`는 호출자가 요청한 백분위(0~100
    정수) -> 그 백분위에 해당하는 `final_equities` 값."""

    final_equities: tuple[Decimal, ...]
    percentiles: dict[int, Decimal]


def run_monte_carlo(
    base_result: QuickBacktestResult,
    *,
    iterations: int,
    rng: np.random.Generator,
    percentiles: tuple[int, ...] = _DEFAULT_PERCENTILES,
) -> MonteCarloResult:
    """`base_result.equity_curve`의 봉별 수익률을 복원추출로 재배열한 경로
    `iterations`개를 만들어 각 경로의 최종 자본을 모은다. 시작 자본은 항상
    `equity_curve[0]`으로 고정한다 — 부트스트랩은 수익률의 순서만 섞지,
    자본 규모 자체를 새로 만들지 않는다."""
    if iterations <= 0:
        raise MonteCarloError(f"iterations는 양수여야 한다: {iterations}")
    for p in percentiles:
        if not 0 <= p <= 100:
            raise MonteCarloError(f"백분위는 0 이상 100 이하여야 한다: {p}")

    returns = _bar_returns(base_result.equity_curve)
    start_equity = float(base_result.equity_curve[0])
    n_returns = len(returns)

    final_equities = np.empty(iterations, dtype=np.float64)
    for i in range(iterations):
        sampled = rng.choice(returns, size=n_returns, replace=True)
        final_equities[i] = start_equity * float(np.prod(1.0 + sampled))

    return MonteCarloResult(
        final_equities=tuple(Decimal(str(v)) for v in final_equities),
        percentiles={
            p: Decimal(str(np.percentile(final_equities, p))) for p in percentiles
        },
    )


def _bar_returns(curve: tuple[Decimal, ...]) -> FloatArray:
    """`curve[i]`가 0이면 그 구간부터의 수익률(`curve[i+1]/curve[i] - 1`)이
    정의되지 않는다 — 자본이 0으로 소진된 경로를 조용히 건너뛰지 않고
    즉시 거부한다(fail-closed)."""
    if len(curve) < 2:
        raise MonteCarloError(
            f"equity_curve 길이가 {len(curve)}다 — 봉 간 수익률을 뽑으려면 최소 2개가 필요하다"
        )
    values = np.array([float(v) for v in curve], dtype=np.float64)
    denom = values[:-1]
    if np.any(denom == 0.0):
        raise MonteCarloError(
            "equity_curve에 0 자본 구간이 있다 — 수익률(0 나눗셈)을 정의할 수 없다"
        )
    return values[1:] / denom - 1.0
