"""109번 §5 compute_metrics() — Sharpe/Sortino/MDD/승률/turnover/gross-net/calmar/exposure.

76번 "bare float 성과값 금지" 원칙에 따라, 계산 불가능한 경우(표본 부족,
표준편차 0)는 조용히 0을 내지 않고 None을 반환한다 — 호출자가 그 한계를
그대로 사용자에게 보여줘야 한다(46번 §2 ValidationResult "한계·가정" 요구).

L31 (spec §2.4 DoD "gross/net split"): docs/specs/L4_strategy_portfolio_
backtest_v1.0.md §2.4 row 121 defines `compute_metrics` as taking a
`gross_equity_curve` (a parallel equity curve recorded without deducting
fee/slippage/funding). That parallel curve is a natural byproduct of
`event_loop.py` (L30's CashLedger-based rewrite), which `run_backtest.py`
has not yet absorbed -- its loop still predates that rewrite (see L30's
D2 evidence, task-3355 note).

This file instead derives the gross figure via an algebraically exact
back-calculation: at fill time, `fee`/`slippage_cost` enter cash as pure
deductions in both directions (adapters/bar_fill_simulator.py -- the
`effective_price` already bakes in slippage, and `fee` is subtracted
separately). So at the final point, `gross_equity == net_equity +
total_fees + total_slippage` is an identity, not an approximation --
exact as long as only the final scalar (not a mid-run gross drawdown)
is needed. `BacktestMetrics` v2 only exposes that final scalar
(`gross_return_pct`), so this back-calculation satisfies the DoD without
threading a second curve through the whole loop. `total_funding` is the
one exception: Phase 1's `simulate_fill.py` does not yet apply funding
cost per fill (BT-8's `domain/costs/funding.py` exists but is not wired
into the fill loop), so it stays `None` (not yet computed) instead of a
silent zero.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import NamedTuple

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import BacktestMetrics, EquityPoint, SimulatedFill
from src.foundation.performance.api import calmar

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


class _RoundTrip(NamedTuple):
    entry: SimulatedFill
    exit: SimulatedFill | None  # None = still open at the end of the run


def _round_trips(fills: list[SimulatedFill]) -> list[_RoundTrip]:
    """Phase 1(단일 종목, 분할청산 없음) 가정 — BUY 다음 SELL이 항상 한
    거래를 닫는다(position.py/portfolio/engine.py와 동일 가정, 08번 §3.6
    PortfolioEngine이 이미 이 조합만 만들도록 강제한다).

    A trailing BUY with no matching SELL is kept with `exit=None`:
    `_round_trip_pnls` skips it (no realized pnl yet), but
    `_exposure_time_pct` counts it as held through the last bar.
    """
    trips: list[_RoundTrip] = []
    entry: SimulatedFill | None = None
    for fill in fills:
        if fill.side == OrderSide.BUY:
            entry = fill
            continue
        if entry is None:
            continue  # 열린 포지션 없이 SELL — 방어적으로 무시(상위 계층 버그 신호)
        trips.append(_RoundTrip(entry=entry, exit=fill))
        entry = None
    if entry is not None:
        trips.append(_RoundTrip(entry=entry, exit=None))
    return trips


def _round_trip_pnls(trips: list[_RoundTrip]) -> list[Decimal]:
    pnls: list[Decimal] = []
    for trip in trips:
        if trip.exit is None:
            continue
        gross = (trip.exit.price - trip.entry.price) * trip.exit.quantity
        costs = trip.entry.fee + trip.entry.slippage_cost + trip.exit.fee + trip.exit.slippage_cost
        pnls.append(gross - costs)
    return pnls


def _exposure_time_pct(trips: list[_RoundTrip], *, total_bars: int) -> Decimal | None:
    """Sum of entry-to-exit bar spans over the total bar span.

    `EquityPoint` does not carry a per-bar position size, so this
    reconstructs exposure from fills' `bar_index` alone. Undefined (None)
    when there is no span to measure (a single-bar run).
    """
    if total_bars < 2:
        return None
    last_bar_index = total_bars - 1
    exposed = 0
    for trip in trips:
        end_bar_index = trip.exit.bar_index if trip.exit is not None else last_bar_index
        exposed += max(0, end_bar_index - trip.entry.bar_index)
    return Decimal(exposed) / Decimal(last_bar_index) * 100


def _annualized_return_pct(
    total_return_pct: Decimal, *, num_periods: int, periods_per_year: int
) -> Decimal | None:
    """Geometric annualization of the whole-period return, in the same
    float-then-`Decimal(str(...))` style as `_sharpe`/`_sortino`. None
    when growth <= 0 (a total wipeout or worse), since the fractional
    root is undefined there.
    """
    if num_periods <= 0:
        return None
    growth = 1 + float(total_return_pct) / 100
    if growth <= 0:
        return None
    annualized = growth ** (periods_per_year / num_periods) - 1
    return Decimal(str(annualized * 100))


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

    trips = _round_trips(fills)
    pnls = _round_trip_pnls(trips)
    win_rate_pct = (
        None if not pnls else Decimal(sum(1 for p in pnls if p > 0)) / Decimal(len(pnls)) * 100
    )

    turnover = (
        Decimal("0")
        if initial_equity <= 0
        else sum((f.price * f.quantity for f in fills), Decimal("0")) / initial_equity
    )

    total_fees = sum((f.fee for f in fills), Decimal("0"))
    total_slippage = sum((f.slippage_cost for f in fills), Decimal("0"))
    net_return_pct = total_return_pct
    gross_return_pct = (
        total_return_pct
        if initial_equity <= 0
        else total_return_pct + (total_fees + total_slippage) / initial_equity * 100
    )

    annualized_return_pct = _annualized_return_pct(
        total_return_pct,
        num_periods=len(equity_curve) - 1,
        periods_per_year=periods_per_year,
    )
    calmar_ratio = (
        None if annualized_return_pct is None else calmar(annualized_return_pct, max_drawdown_pct)
    )
    exposure_time_pct = _exposure_time_pct(trips, total_bars=len(equity_curve))

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
        gross_return_pct=gross_return_pct,
        net_return_pct=net_return_pct,
        total_fees=total_fees,
        total_slippage=total_slippage,
        total_funding=None,  # Phase 1 simulate_fill() does not apply funding yet (module docstring)
        calmar_ratio=calmar_ratio,
        exposure_time_pct=exposure_time_pct,
        annualization=periods_per_year,
    )
