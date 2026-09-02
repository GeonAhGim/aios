"""109번 §4 재생 루프 — 실시간 평가 로직(StrategyEngine/PortfolioEngine/
ExecutionEquityTracker)을 과거 bar 시퀀스에 그대로 재적용한다.

Phase 1 범위(position.py/portfolio/engine.py와 동일 가정): 단일 종목,
분할청산 없음, 스탑로스/전량청산만. 여러 심볼 동시 백테스트나
walk-forward/Monte Carlo는 이 함수를 반복 호출하는 상위 오케스트레이션
(109번 §5, 이 파일의 범위 밖)의 몫이다.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import NamedTuple

from src.core.indicators.talib_adapter import IndicatorService
from src.core.portfolio.engine import PortfolioEngine, PortfolioEngineError
from src.core.strategy.condition_evaluator import IndicatorDataMissingError
from src.core.strategy.engine import StrategyEngine
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig
from src.data.models.trading import OrderSide
from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.application.simulate_fill import simulate_fill
from src.foundation.backtest.domain.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    SimulatedFill,
)
from src.foundation.backtest.domain.rules import has_enough_warmup, warn_if_zero_cost
from src.services.condition_compiler import ORDER_FILLED
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.market_state import build_market_state

_SYNTHETIC_EXECUTION_ID = -1  # 실제 실행 execution_id(항상 양수)와 절대 겹치지 않는 고정 음수


class BacktestRunError(Exception):
    """입력 자체가 이 엔진의 불변조건을 만족하지 못할 때(warmup 부족 등)."""


class _PendingOrder(NamedTuple):
    """신호가 나온 bar와 실제 체결 bar 사이(§5 look-ahead 방지)의 대기 상태.

    도메인 계약(domain/models.py)에는 넣지 않는다 — 이 루프 내부에서만
    쓰는 일시적 상태라 외부에 노출할 계약이 아니다."""

    side: OrderSide
    quantity: Decimal
    filled_state: FSMState  # ORDER_FILLED 전이가 실제로 향하는 상태(§_order_filled_target)


def _order_filled_target(fsm_config: FSMStrategyConfig, from_state: FSMState) -> FSMState:
    """StrategyEngine.evaluate()는 ORDER_FILLED 조건 전이를 의도적으로
    건너뛴다(FD-4.2 몫 — engine.py 51-53행 주석) — 백테스트에는 별도의
    체결확인 이벤트가 없으므로, 이 엔진이 그 지점에서 그 전이를 직접
    찾아 적용해야 한다."""
    for transition in fsm_config.transitions:
        if transition.from_state == from_state and transition.condition == ORDER_FILLED:
            return transition.to_state
    raise BacktestRunError(
        f"{from_state}에서 ORDER_FILLED로 나가는 전이가 FSM에 없습니다 — "
        "컴파일러 계약(condition_compiler.py) 위반입니다."
    )


def run_backtest(
    config: BacktestConfig,
    fsm_config: FSMStrategyConfig,
    bars: list[Candle],
    *,
    indicator_service: IndicatorService | None = None,
) -> BacktestResult:
    if not has_enough_warmup(total_bars=len(bars), warmup_bars=config.warmup_bars):
        raise BacktestRunError(
            f"bar 개수({len(bars)})가 warmup_bars({config.warmup_bars})를 초과하지 않습니다."
        )

    warnings: list[str] = []
    zero_cost_warning = warn_if_zero_cost(config.cost_model)
    if zero_cost_warning:
        warnings.append(zero_cost_warning)

    service = indicator_service or IndicatorService()
    strategy_engine = StrategyEngine()
    portfolio_engine = PortfolioEngine()

    current_bar_date: list[date] = [bars[0].open_time.date()]
    equity_tracker = ExecutionEquityTracker(today=lambda: current_bar_date[0])

    fsm_state = fsm_config.initial_state
    position_quantity = Decimal("0")
    cash = config.initial_equity
    equity_curve: list[EquityPoint] = []
    fills: list[SimulatedFill] = []
    pending: _PendingOrder | None = None

    for bar_index, bar in enumerate(bars):
        if pending is not None:
            fill = simulate_fill(
                bar=bar,
                bar_index=bar_index,
                side=pending.side,
                quantity=pending.quantity,
                cost_model=config.cost_model,
            )
            fills.append(fill)
            if fill.side == OrderSide.BUY:
                cash -= fill.price * fill.quantity + fill.fee
                position_quantity += fill.quantity
            else:
                cash += fill.price * fill.quantity - fill.fee
                position_quantity -= fill.quantity
            fsm_state = pending.filled_state
            pending = None

        current_bar_date[0] = bar.open_time.date()
        equity = cash + position_quantity * bar.close
        _daily_pnl_pct, drawdown_pct = equity_tracker.record(_SYNTHETIC_EXECUTION_ID, equity)
        equity_curve.append(
            EquityPoint(
                bar_index=bar_index,
                timestamp=bar.close_time,
                equity=equity,
                drawdown_pct=drawdown_pct,
            )
        )

        if bar_index < config.warmup_bars:
            continue

        window = bars[: bar_index + 1]
        market_state = build_market_state(fsm_config, window, indicator_service=service)
        try:
            signal = strategy_engine.evaluate(
                fsm_config,
                market_state,
                execution_id=_SYNTHETIC_EXECUTION_ID,
                fsm_state=fsm_state,
            )
        except IndicatorDataMissingError:
            signal = None
        if signal is None:
            continue

        portfolio_state = {
            "allocated_capital": config.initial_equity,
            "position_quantity": position_quantity,
            "current_price": bar.close,
            "total_equity": equity,
        }
        try:
            decision = portfolio_engine.allocate(signal, portfolio_state)
        except PortfolioEngineError as exc:
            warnings.append(f"bar {bar_index}: PortfolioEngine 예외 — {exc}")
            continue
        if decision is None:
            continue

        pending = _PendingOrder(
            side=signal.direction,
            quantity=decision.approved_quantity,
            filled_state=_order_filled_target(fsm_config, signal.to_state),
        )

    metrics = compute_metrics(
        equity_curve=equity_curve,
        fills=fills,
        initial_equity=config.initial_equity,
        periods_per_year=config.periods_per_year,
    )
    return BacktestResult(
        config=config,
        fills=fills,
        equity_curve=equity_curve,
        metrics=metrics,
        warnings=warnings,
    )
