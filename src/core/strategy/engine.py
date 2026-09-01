"""FD-8.1 — 전략 신호 생성 (StrategyEngine).

Spec: 기능설계문서_v1.21.md#FD-8.1, 03_core_modules_v1.1.md#§3.5

ADR-2026-08-29-E — PAPER 모드 한정 실동작 구현. 8.2-A Master Authority —
이 클래스는 "의도"(Signal)만 만든다. 실제 주문 여부·수량은 FD-8.2/8.3의
책임이며, 이 클래스가 직접 Executor를 호출하는 경로는 존재하지 않는다.
LLM/Agent 판단은 어디에도 개입하지 않는다 — FSMStrategyConfig가 컴파일
시점에 고정한 조건식과 현재 시세만으로 결정론적으로 판단한다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from src.core.strategy.condition_evaluator import ConditionEvaluator, IndicatorDataMissingError
from src.core.strategy.models import Signal
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.data.models.trading import OrderSide
from src.services.condition_compiler import ORDER_FILLED

logger = logging.getLogger(__name__)

_SELL_TARGETS = (FSMState.SELL_ORDER_PENDING, FSMState.STOP_LOSS)


class StrategyEngine:
    def __init__(self, *, evaluator: ConditionEvaluator | None = None) -> None:
        self._evaluator = evaluator or ConditionEvaluator()
        # 실행별 최근 1틱 시장상태 캐시 — crosses_above/crosses_below 평가에 필요
        # (PreviewCalculator.prev_value와 동일 원칙). 실행 하나당 FSM 하나이므로
        # execution_id를 키로 쓴다.
        self._prev_tick_cache: dict[int, dict[str, float]] = {}

    def evaluate(
        self,
        fsm_config: FSMStrategyConfig,
        market_state: dict[str, float],
        *,
        execution_id: int,
        fsm_state: FSMState,
    ) -> Signal | None:
        """FD-8.1 처리단계. `execution_id`/`fsm_state`는 03번 §3.5 시그니처를
        확장하는 키워드 전용 인자다(FD-8.0 실행상태 추적 없이는 "지금 어느
        전이를 평가해야 하는가" 자체를 알 수 없어 필수) — fsm_config/
        market_state 위치 인자는 그대로 유지해 인터페이스 변경 없음
        원칙(ADR-2026-08-29-E)을 지킨다."""
        prev_market_state = self._prev_tick_cache.get(execution_id)

        # ORDER_FILLED 예약 리터럴 전이(BUY_ORDER_PENDING→HOLDING 등)는 이
        # 함수가 아니라 FD-4.2(주문 체결 확인)가 트리거한다 — 조건 자체를
        # 걸러내면 IDLE/HOLDING 이외 상태는 자연히 평가 대상이 없어진다.
        candidates = [
            t
            for t in fsm_config.transitions
            if t.from_state == fsm_state and t.condition != ORDER_FILLED
        ]
        # HOLDING에서 exit/stop_loss 둘 다 후보일 때 stop_loss가 우선(손절이
        # 항상 익절보다 안전 우선) — STOP_LOSS로 가는 전이를 먼저 평가한다.
        candidates.sort(key=lambda t: 0 if t.to_state == FSMState.STOP_LOSS else 1)

        signal: Signal | None = None
        for transition in candidates:
            try:
                matched = self._evaluator.evaluate(
                    transition.condition, market_state, prev_market_state
                )
            except IndicatorDataMissingError as exc:
                logger.warning(
                    "StrategyEngine: 지표 데이터 부족(execution_id=%s, key=%s) — "
                    "판단 보류(신호 없음으로 처리)",
                    execution_id,
                    exc,
                )
                continue
            if matched:
                is_sell = transition.to_state in _SELL_TARGETS
                direction = OrderSide.SELL if is_sell else OrderSide.BUY
                signal = Signal(
                    strategy_id=fsm_config.strategy_id,
                    strategy_version=fsm_config.version,
                    symbol=fsm_config.target_asset,
                    direction=direction,
                    confidence=1.0,
                    target_position=Decimal("0"),
                    stop_loss=None,
                    take_profit=None,
                    timestamp=datetime.now(timezone.utc),
                    to_state=transition.to_state,
                )
                break

        self._prev_tick_cache[execution_id] = dict(market_state)
        return signal
