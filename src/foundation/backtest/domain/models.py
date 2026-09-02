"""Backtest Simulation Engine 도메인 모델 — DB/HTTP 없이 순수 데이터.

Spec: AIOSproject 109_backtest_simulation_engine_l3_build_and_operational_
specification_v1.0.md §3, §5.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.data.models.trading import OrderSide


class CostModel(BaseModel):
    """46번 §2 "Backtest" 행 — 비용모델 없는 백테스트는 거부 대상이라
    기본값을 두지 않는다(호출자가 반드시 명시적으로 선택하게 강제).

    v1은 선형 모델(고정 bps)만 지원한다 — 호가창 깊이/시장충격 기반
    비선형 슬리피지는 후속 revision 대상(46번 §2 Capacity 행)."""

    fee_bps: Decimal
    slippage_bps: Decimal


class BacktestConfig(BaseModel):
    """재생 1회 실행에 필요한 모든 입력 — 105번 원칙에 따라 실행 전
    고정(pinned)된다. `warmup_bars`는 지표가 유효해지기 전 구간을
    신호평가에서 제외하는 데 쓴다(예: SMA(20)이면 최소 20)."""

    strategy_id: str
    strategy_version: str
    initial_equity: Decimal
    cost_model: CostModel
    warmup_bars: int = Field(ge=0)
    periods_per_year: int = Field(gt=0)
    """Sharpe/Sortino 연환산 계수 — bar timeframe에 맞춰 호출자가 지정한다
    (예: 일봉이면 252, 1시간봉이면 365*24). 엔진이 timeframe 문자열을
    파싱해 추측하지 않는다 — 추측이 틀리면 조용히 틀린 지표를 만들기
    때문에(46번 §2 "unit/annualization convention" 필수 표기 원칙)."""


class SimulatedFill(BaseModel):
    bar_index: int
    timestamp: datetime
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    slippage_cost: Decimal


class EquityPoint(BaseModel):
    bar_index: int
    timestamp: datetime
    equity: Decimal
    drawdown_pct: Decimal


class BacktestMetrics(BaseModel):
    """76번 "bare float 성과값 금지" 원칙 — 모든 값에 단위/기간을
    필드명으로 명시한다. `sharpe_ratio`/`sortino_ratio`는 표본이 2개
    미만이거나 표준편차가 0이면 계산 불가라 None(46번이 요구하는
    "한계·가정"의 최소 구현 — 조용히 0을 내지 않는다)."""

    period_start: datetime
    period_end: datetime
    total_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal


class BacktestResult(BaseModel):
    config: BacktestConfig
    fills: list[SimulatedFill]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
    warnings: list[str] = Field(default_factory=list)
