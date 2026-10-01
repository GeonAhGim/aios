"""Volatility, MDD, Sharpe, Calmar — pure functions.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6 — "extract as
pure functions to share the same definition with backtest.compute_metrics"
(prevents the two contexts from independently reimplementing the same
formula and subtly diverging).

All functions take a list of period returns as input — annualization always
requires an explicit `periods_per_year` argument (no implicit assumptions,
consistent with the "typed input" principle in #73 §6 and #78)."""

from __future__ import annotations

from decimal import Decimal


def period_returns(values: list[Decimal]) -> list[Decimal]:
    """Simple returns between consecutive values. Skips intervals where the
    base is 0 (treated as missing rather than a division error — callers can
    infer missingness from result length)."""
    returns: list[Decimal] = []
    for prev, cur in zip(values, values[1:], strict=False):
        if prev == 0:
            continue
        returns.append(cur / prev - 1)
    return returns


def annualized_vol(returns: list[Decimal], *, periods_per_year: int) -> Decimal | None:
    if len(returns) < 2:
        return None
    mean = sum(returns, Decimal(0)) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (len(returns) - 1)
    return variance.sqrt() * Decimal(periods_per_year).sqrt()


def max_drawdown(values: list[Decimal]) -> Decimal | None:
    """Returns a positive decimal (0.15 = 15% drawdown). Returns None if
    values are empty."""
    if not values:
        return None
    peak = values[0]
    worst = Decimal(0)
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            drawdown = (peak - v) / peak
            if drawdown > worst:
                worst = drawdown
    return worst


def sharpe(returns: list[Decimal], *, rf: Decimal, periods_per_year: int) -> Decimal | None:
    """`rf` is the per-period (not annualized) risk-free rate — callers must
    divide an annual rate by `periods_per_year` before passing it (no implicit
    conversion)."""
    if len(returns) < 2:
        return None
    vol = annualized_vol(returns, periods_per_year=periods_per_year)
    if vol is None or vol == 0:
        return None
    excess = [r - rf for r in returns]
    mean_excess = sum(excess, Decimal(0)) / len(excess)
    annualized_mean_excess = mean_excess * periods_per_year
    return annualized_mean_excess / vol


def calmar(annualized_return: Decimal | None, mdd: Decimal | None) -> Decimal | None:
    if annualized_return is None or mdd is None or mdd == 0:
        return None
    return annualized_return / mdd
