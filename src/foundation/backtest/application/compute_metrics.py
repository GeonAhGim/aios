"""109번 §5 compute_metrics() — Sharpe/Sortino/MDD/승률/turnover.

76번 "bare float 성과값 금지" 원칙에 따라, 계산 불가능한 경우(표본 부족,
표준편차 0)는 조용히 0을 내지 않고 None을 반환한다 — 호출자가 그 한계를
그대로 사용자에게 보여줘야 한다(46번 §2 ValidationResult "한계·가정" 요구).
"""
from __future__ import annotations

import statistics
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import BacktestMetrics, EquityPoint, SimulatedFill

_ANNUALIZATION_MIN_SAMPLES = 2  # stdev 계산에 필요한 최소 수익률 표본 수


def _bar_returns(equity_curve: list[EquityPoint]) -> list[float]:
    returns: list[float] = []
    for prev, curr in zip(equity_curve, equity_curve[1:], strict=False):
        if prev.equity <= 0:
            continue
        returns.append(float((curr.equity - prev.equity) / prev.equity))
    return returns


def _sharpe(returns: list[float], *, periods_per_year: int) -> Decimal | None:
    if len(returns) < _ANNUALIZATION_MIN_SAMPLES:
        return None
    stdev = statistics.stdev(returns)
    if stdev == 0:
        return None
    mean = statistics.mean(returns)
    return Decimal(str(mean / stdev * (periods_per_year**0.5)))


def _sortino(returns: list[float], *, periods_per_year: int) -> Decimal | None:
    if len(returns) < _ANNUALIZATION_MIN_SAMPLES:
        return None
    downside = [r for r in returns if r < 0]
    if len(downside) < _ANNUALIZATION_MIN_SAMPLES:
        return None  # 하락 구간 표본 부족 — downside deviation을 신뢰할 수 없다
    downside_stdev = statistics.stdev(downside)
    if downside_stdev == 0:
        return None
    mean = statistics.mean(returns)
    return Decimal(str(mean / downside_stdev * (periods_per_year**0.5)))


def _round_trip_pnls(fills: list[SimulatedFill]) -> list[Decimal]:
    """Phase 1(단일 종목, 분할청산 없음) 가정 — BUY 다음 SELL이 항상 한
    거래를 닫는다(position.py/portfolio/engine.py와 동일 가정, 08번 §3.6
    PortfolioEngine이 이미 이 조합만 만들도록 강제한다)."""
    pnls: list[Decimal] = []
    entry: SimulatedFill | None = None
    for fill in fills:
        if fill.side == OrderSide.BUY:
            entry = fill
            continue
        if entry is None:
            continue  # 열린 포지션 없이 SELL — 방어적으로 무시(상위 계층 버그 신호)
        gross = (fill.price - entry.price) * fill.quantity
        costs = entry.fee + entry.slippage_cost + fill.fee + fill.slippage_cost
        pnls.append(gross - costs)
        entry = None
    return pnls


def compute_metrics(
    *,
    equity_curve: list[EquityPoint],
    fills: list[SimulatedFill],
    initial_equity: Decimal,
    periods_per_year: int,
) -> BacktestMetrics:
    if not equity_curve:
        raise ValueError("빈 equity_curve로는 지표를 계산할 수 없습니다.")

    final_equity = equity_curve[-1].equity
    total_return_pct = (
        Decimal("0")
        if initial_equity <= 0
        else (final_equity - initial_equity) / initial_equity * 100
    )
    max_drawdown_pct = max((ep.drawdown_pct for ep in equity_curve), default=Decimal("0"))

    returns = _bar_returns(equity_curve)
    sharpe_ratio = _sharpe(returns, periods_per_year=periods_per_year)
    sortino_ratio = _sortino(returns, periods_per_year=periods_per_year)

    pnls = _round_trip_pnls(fills)
    win_rate_pct = (
        None
        if not pnls
        else Decimal(sum(1 for p in pnls if p > 0)) / Decimal(len(pnls)) * 100
    )

    turnover = (
        Decimal("0")
        if initial_equity <= 0
        else sum((f.price * f.quantity for f in fills), Decimal("0")) / initial_equity
    )

    return BacktestMetrics(
        period_start=equity_curve[0].timestamp,
        period_end=equity_curve[-1].timestamp,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        win_rate_pct=win_rate_pct,
        total_trades=len(pnls),
        turnover=turnover,
    )
