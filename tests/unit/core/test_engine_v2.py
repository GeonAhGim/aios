"""L12 D2 증빙 — StrategyEngine(src/core/strategy/engine.py).

engine.py는 FROZEN_PAPER_ONLY이고 이 task(decision)에 명시적 FROZEN 승인이
없어 소스는 수정하지 않는다. §9 L12 DoD 중 "프로세스 캐시 제거 확인
(`_prev_tick_cache` grep 0)"은 N/A(FROZEN 미승인)로 남긴다 — 그 결함이
실재함은 아래 게이트 적색 재현 테스트로 증명하되, 제거 자체는 별도 FROZEN
승인 이후 L13(영속 StrategyStateMemory 배선)과 함께 처리한다.

이 파일은 기존 `tests/unit/core/test_strategy_engine.py`(FD-8.1 회귀, 그대로
유지)와 겹치지 않는 D2 하한 증빙만 추가한다: negative ≥3, 실패 주입 1,
성능 단언 1, 게이트 적색 재현 1 (ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L12.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from src.core.strategy.condition_evaluator import (
    ConditionEvaluationError,
    ConditionEvaluator,
)
from src.core.strategy.engine import StrategyEngine
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.services.condition_compiler import ORDER_FILLED, ConditionCompiler
from src.services.preview_service import PreviewCondition


def _compile_config() -> FSMStrategyConfig:
    compiler = ConditionCompiler()
    return compiler.compile(
        strategy_id="strat-v2",
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


def _manual_config(transitions: list[FSMTransition]) -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="strat-manual",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING, FSMState.HOLDING],
        transitions=transitions,
        author_agent="test",
    )


# --- negative (>= 3) ---------------------------------------------------


def test_negative_empty_transitions_returns_none_without_raising():
    engine = StrategyEngine()
    config = _manual_config([])

    signal = engine.evaluate(config, {"RSI": 25.0}, execution_id=500, fsm_state=FSMState.IDLE)

    assert signal is None


def test_negative_order_filled_literal_never_self_triggers_even_when_from_state_matches():
    """ORDER_FILLED 리터럴 전이는 FD-4.2(주문 체결 확인)만 트리거할 수 있다 —
    from_state가 일치해도 engine이 스스로 이 전이를 후보로 평가하면 안 된다."""
    engine = StrategyEngine()
    transitions = [
        FSMTransition(
            from_state=FSMState.BUY_ORDER_PENDING,
            to_state=FSMState.HOLDING,
            condition=ORDER_FILLED,
        ),
    ]
    config = _manual_config(transitions)

    signal = engine.evaluate(
        config, {"RSI": 25.0}, execution_id=501, fsm_state=FSMState.BUY_ORDER_PENDING
    )

    assert signal is None


def test_negative_draft_contract_never_deviates_confidence_or_target_position():
    """8.2-A Master Authority — StrategyEngine은 '의도'만 만든다. 신호가
    나가더라도 confidence/target_position/stop_loss/take_profit이 Draft 값
    (1.0 / 0 / None / None)을 벗어나면 PortfolioEngine의 권한을 침범한 것이다."""
    engine = StrategyEngine()
    config = _compile_config()

    signal = engine.evaluate(config, {"RSI": 25.0}, execution_id=502, fsm_state=FSMState.IDLE)

    assert signal is not None
    assert signal.confidence == 1.0
    assert signal.target_position == Decimal("0")
    assert signal.stop_loss is None
    assert signal.take_profit is None


def test_negative_deterministic_first_match_when_no_stop_loss_tiebreak():
    """stop_loss가 후보에 없을 때는 sort가 안정적으로 원래 선언 순서를
    보존해야 한다 — 재현성(§1 재현성 요구)이 걸린 동작이라 순서가 흔들리면
    같은 입력에서 다른 백테스트 결과가 나올 수 있다."""
    engine = StrategyEngine()
    transitions = [
        FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="RSI < 40.0"),
        FSMTransition(
            from_state=FSMState.IDLE, to_state=FSMState.BUY_ORDER_PENDING, condition="RSI < 40.0"
        ),
    ]
    config = _manual_config(transitions)

    signal = engine.evaluate(config, {"RSI": 10.0}, execution_id=503, fsm_state=FSMState.IDLE)

    assert signal is not None
    assert signal.to_state == FSMState.HOLDING  # 선언 순서상 첫 번째 후보


# --- 실패 주입 (1) --------------------------------------------------------


def test_failure_injection_unsupported_operator_propagates_fail_closed():
    """컴파일러가 절대 만들지 않는 연산자(`!=`)가 condition에 섞여 들어오는
    것은 상류(컴파일러/저장소) 손상 신호다. engine은 IndicatorDataMissingError
    "만" 판단 보류로 삼키고 그 외 오류는 삼키지 않는다 — 조용히 '신호 없음'으로
    위장하면 손상된 전략이 아무 문제 없는 것처럼 계속 실행된다(fail-closed 위반)."""
    engine = StrategyEngine()
    transitions = [
        FSMTransition(from_state=FSMState.IDLE, to_state=FSMState.HOLDING, condition="RSI != 30.0"),
    ]
    config = _manual_config(transitions)

    try:
        engine.evaluate(config, {"RSI": 25.0}, execution_id=504, fsm_state=FSMState.IDLE)
    except ConditionEvaluationError:
        pass
    else:
        raise AssertionError(
            "ConditionEvaluationError가 삼켜짐 — engine이 손상된 조건식을 '신호 없음'으로 위장했다"
        )


# --- 성능 단언 (1) --------------------------------------------------------


@pytest.mark.perf
def test_performance_evaluate_p99_latency_within_pretrade_gate_budget():
    """ADR-2026-09-09-C Decision 1 예산: 사전거래 게이트 p99 5ms.
    StrategyEngine.evaluate는 주문 제출 이전(신호 생성) 경로이므로 이 예산이
    적용된다."""
    engine = StrategyEngine()
    config = _compile_config()

    n = 500
    latencies_ms: list[float] = []
    for i in range(n):
        start = time.perf_counter()
        engine.evaluate(config, {"RSI": 50.0}, execution_id=1000 + i, fsm_state=FSMState.IDLE)
        latencies_ms.append((time.perf_counter() - start) * 1000)

    latencies_ms.sort()
    p99 = latencies_ms[int(n * 0.99) - 1]

    assert p99 < 5.0, f"p99={p99:.3f}ms — 사전거래 게이트 5ms 예산 초과"


# --- 게이트 적색 재현 (1) --------------------------------------------------


def test_gate_red_process_cache_loses_crossover_state_on_restart():
    """L12 DoD가 요구하는 '_prev_tick_cache 제거'가 아직 반영되지 않았음을
    실제로 재현한다(적색). engine.py는 execution_id별 이전 시세를 프로세스
    메모리 dict(`_prev_tick_cache`)에만 들고 있다 — 워커 프로세스가
    재시작되면(=새 StrategyEngine 인스턴스) 실제로는 유효한 crossover 이력이
    있어도 이를 복구할 방법이 없다.

    초록(있어야 할 그림, L08 StrategyStateMemory가 설계한 대로 영속 prev_values를
    그대로 재사용하는 경우)과 적색(engine.py의 실제 재시작 후 동작)을
    같은 시나리오로 대조한다. 제거는 FROZEN 승인 이후 L13과 함께 처리한다
    (N/A(FROZEN 미승인) — src/core/strategy/engine.py는 FROZEN_PAPER_ONLY).
    """
    # 초록: "이전 프로세스"가 영속 상태(L08 StrategyStateMemory)에 RSI=15.0
    # (crosses_below 20 대기 이력)을 이미 저장해 뒀다고 가정하면, 그 값을 그대로
    # 넘기는 것만으로 crosses_below 20이 올바르게 판정된다.
    green = ConditionEvaluator().evaluate("RSI CROSSES_BELOW 20.0", {"RSI": 10.0}, {"RSI": 25.0})
    assert green is True

    # 적색: 완전히 동일한 시나리오(직전 RSI=25.0 -> 이번 RSI=10.0, crosses_below
    # 20)를 engine.py에 태우되, "재시작"을 새 StrategyEngine 인스턴스로 흉내낸다.
    # 실제 운영에서는 재시작 직전에 이미 RSI=25.0 관측 이력이 있었을 것이지만,
    # `_prev_tick_cache`가 프로세스 메모리에만 있어 새 인스턴스는 그 이력을
    # 절대 복구할 수 없다 — 첫 호출의 prev_market_state는 항상 None으로
    # 시작한다.
    config = _compile_config()
    fresh_after_restart = StrategyEngine()
    lost_signal = fresh_after_restart.evaluate(
        config, {"RSI": 10.0}, execution_id=999, fsm_state=FSMState.HOLDING
    )
    assert lost_signal is None  # 적색: green과 동일한 이력이 있었어도 재시작으로 유실됨
