"""L10 D2 증빙 -- src/core/strategy/confidence.py.

negative >= 3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1
(ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L10.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.strategy.confidence import EvalResult, compute_confidence
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig

# --- 산식 정확값 -----------------------------------------------------------


def test_all_crossover_leaves_satisfied_yields_full_confidence():
    result = EvalResult(
        matched=True, satisfied_leaves=2, total_leaves=2, crossover_leaves_satisfied=2
    )
    assert compute_confidence(result) == Decimal("1.0000")


def test_all_comparison_leaves_satisfied_yields_half_weight():
    result = EvalResult(
        matched=True, satisfied_leaves=2, total_leaves=2, crossover_leaves_satisfied=0
    )
    assert compute_confidence(result) == Decimal("0.5000")


def test_mixed_leaves_weighted_exact_value():
    # 1 crossover(가중 1.0) + 1 comparison(가중 0.5) satisfied, total 4 리프.
    result = EvalResult(
        matched=False, satisfied_leaves=2, total_leaves=4, crossover_leaves_satisfied=1
    )
    # weighted = 1*1.0 + 1*0.5 = 1.5; confidence = 1.5/4 = 0.375
    assert compute_confidence(result) == Decimal("0.3750")


def test_no_leaves_satisfied_yields_zero():
    result = EvalResult(
        matched=False, satisfied_leaves=0, total_leaves=3, crossover_leaves_satisfied=0
    )
    assert compute_confidence(result) == Decimal("0.0000")


def test_rounding_to_four_places_half_up():
    # weighted = 1*0.5 = 0.5; confidence = 0.5/3 = 0.1666...  -> ROUND_HALF_UP -> 0.1667
    result = EvalResult(
        matched=False, satisfied_leaves=1, total_leaves=3, crossover_leaves_satisfied=0
    )
    assert compute_confidence(result) == Decimal("0.1667")


# --- negative (>= 3) -------------------------------------------------------


def test_negative_construction_rejects_negative_satisfied_leaves():
    with pytest.raises(ValidationError):
        EvalResult(matched=False, satisfied_leaves=-1, total_leaves=3, crossover_leaves_satisfied=0)


def test_negative_zero_total_leaves_rejected():
    result = EvalResult(
        matched=False, satisfied_leaves=0, total_leaves=0, crossover_leaves_satisfied=0
    )
    with pytest.raises(ValueError, match="total_leaves must be positive"):
        compute_confidence(result)


def test_negative_satisfied_exceeds_total_rejected():
    result = EvalResult(
        matched=False, satisfied_leaves=5, total_leaves=3, crossover_leaves_satisfied=0
    )
    with pytest.raises(ValueError, match="satisfied_leaves must be within"):
        compute_confidence(result)


# --- 실패 주입 (1) ----------------------------------------------------------


def test_failure_injection_corrupted_upstream_result_fails_closed():
    """상류(향후 DSL-8 tree walker)가 내부 불일치 카운트(crossover >
    satisfied)를 만들어낸 경우를 `model_construct`로 검증기를 우회해
    흉내낸다 -- pydantic 검증기만 믿으면 이런 손상된 객체가 그대로
    통과해 잘못된 confidence(가짜 신뢰도)를 조용히 만들어낼 수 있다.
    compute_confidence는 이 경우에도 예외 없이 값을 반환해서는 안 된다
    (fail-closed)."""
    corrupted = EvalResult.model_construct(
        matched=True, satisfied_leaves=1, total_leaves=3, crossover_leaves_satisfied=2
    )
    with pytest.raises(ValueError, match="crossover_leaves_satisfied must be within"):
        compute_confidence(corrupted)


# --- 성능 단언 (1) ----------------------------------------------------------


def test_performance_compute_confidence_p99_latency_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms.
    confidence 산출은 신호 생성 경로(주문 제출 이전)의 일부다."""
    result = EvalResult(
        matched=True, satisfied_leaves=7, total_leaves=10, crossover_leaves_satisfied=3
    )

    n = 1000
    latencies_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        compute_confidence(result)
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(n * 0.99) - 1]
    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"


# --- 게이트 적색 재현 (1) ----------------------------------------------------


def test_gate_red_engine_confidence_ignores_actual_leaf_ratio_today():
    """§1.2 감사 인용: engine.py는 `confidence=1.0` 상수를 반환한다 --
    실제로 몇 개의 조건 리프가 만족됐는지와 무관하게 항상 1.0이다(적색).
    confidence.py는 EvalResult로부터 실제 가중 리프 비율을 정확히 계산한다
    (초록) -- 배선(engine.py가 이 계산을 실제로 호출하도록 바꾸는 것)은
    tree_evaluator(L09/DSL-8)가 EvalResult를 실제로 만들어낸 뒤 별도
    리프에서 처리한다(FROZEN 승인 범위: 신규 파일 신설만, engine.py 수정
    불가)."""
    from src.services.condition_compiler import ConditionCompiler
    from src.services.preview_service import PreviewCondition

    config: FSMStrategyConfig = ConditionCompiler().compile(
        strategy_id="strat-confidence-gate",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[
            PreviewCondition(indicator="RSI", operator="crosses_below", threshold=20.0)
        ],
    )

    # 적색: engine.py는 딱 1개짜리 단일 리프 조건에서도, 조건이 크게 여유
    # 있게 만족되든(RSI=5, 임계값 30과 거리 멂) 간신히 만족되든 구분 없이
    # confidence=1.0을 반환한다.
    engine = StrategyEngine()
    signal = engine.evaluate(config, {"RSI": 5.0}, execution_id=9001, fsm_state=FSMState.IDLE)
    assert signal is not None
    assert signal.confidence == 1.0  # 적색: 실제 리프 비율과 무관한 상수

    # 초록: 같은 시나리오를 EvalResult(비교 리프 1개 만족/1개 전체)로
    # 표현하면 confidence.py는 가중 리프 비율(0.5, 비교 리프 가중치)을
    # 정확히 산출한다.
    green_result = EvalResult(
        matched=True, satisfied_leaves=1, total_leaves=1, crossover_leaves_satisfied=0
    )
    assert compute_confidence(green_result) == Decimal("0.5000")
    assert compute_confidence(green_result) != Decimal(str(signal.confidence))
