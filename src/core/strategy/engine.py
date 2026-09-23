"""FD-8.1 — Strategy signal generation (StrategyEngine).

Spec: functional_design_v1.21.md#FD-8.1, 03_core_modules_v1.1.md#§3.5

ADR-2026-08-29-E — Paper-mode-only runtime implementation. 8.2-A Master Authority —
This class produces only "intent" (Signal). Actual order placement and sizing
are the responsibility of FD-8.2/8.3; this class has no path that directly
calls Executor. No LLM/Agent judgment is involved — decisions are made
deterministically from conditions fixed at compile time by FSMStrategyConfig
and current market prices alone.
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
        # Per-execution cache of the most recent 1-tick market state — needed for
        # crosses_above/crosses_below evaluation (same principle as
        # PreviewCalculator.prev_value). One FSM per execution, so we key by
        # execution_id.
        self._prev_tick_cache: dict[int, dict[str, float]] = {}

    def evaluate(
        self,
        fsm_config: FSMStrategyConfig,
        market_state: dict[str, float],
        *,
        execution_id: int,
        fsm_state: FSMState,
    ) -> Signal | None:
        """FD-8.1 processing step. `execution_id`/`fsm_state` are keyword-only
        arguments extending the 03 §3.5 signature (without FD-8.0 execution
        state tracking, we cannot know "which transition to evaluate now",
        making them essential) — keeping fsm_config/market_state as positional
        arguments preserves the no-interface-change principle
        (ADR-2026-08-29-E)."""
        prev_market_state = self._prev_tick_cache.get(execution_id)

        # ORDER_FILLED literal transitions (BUY_ORDER_PENDING→HOLDING, etc.) are
        # triggered by FD-4.2 (order-fill confirmation), not this function —
        # filtering them out here leaves no non-IDLE/HOLDING states as candidates.
        candidates = [
            t
            for t in fsm_config.transitions
            if t.from_state == fsm_state and t.condition != ORDER_FILLED
        ]
        # When both exit and stop_loss are candidates in HOLDING state, stop_loss
        # takes priority (loss-cut is always safety-first) — evaluate transitions
        # to STOP_LOSS first.
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
