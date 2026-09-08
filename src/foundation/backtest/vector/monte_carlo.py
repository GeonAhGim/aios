"""BT-16a (3/3) — `backtest/vector/monte_carlo.py`: per-bar-return resampling Monte Carlo.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16. task-2371 decision -- same split as the `grid.py` module docstring (the AI-10
experiment-ledger record is deferred to BT-16b, this leaf is execution-only).

`grid.py`/`walk_forward.py` call the event engine (`run_vector_backtest`) again for
every combo/window to produce new fills. This module is different -- it treats the
single `QuickBacktestResult.equity_curve` (a Decimal tuple, per-bar ledger equity)
BT-15b already produced as ground truth and does not recompute fills or costs (I-05, no
re-invocation of the event engine). It extracts bar-to-bar returns from `equity_curve`
and builds `iterations` reorderings via bootstrap resampling to quantify "how much does
this strategy's final performance depend on the realized bar order" -- since it's not
changing parameters or the window, only reshuffling the same return sample, there's no
cost to re-running the event loop (this leaf satisfies the BT-16 DoD "1,000 combos
<=60s" with pure numpy vector operations).

Determinism: the random generator is taken explicitly as the `numpy.random.Generator`
argument -- depending on global random state (e.g. `np.random.seed`) would make the same
call produce different results across runs, leaving no way to tie it to a reproducibility
key (BT-9). Passing a `Generator` with a fixed seed makes this function always return the
same result.

The `Decimal` round-trip (`Decimal(str(float))`) is the same deliberate precision
trade-off already declared by the `arrays.py` module docstring -- this leaf is a
statistical distribution summary, not a fill log that needs accounting precision.

Pure module -- no I/O (the random generator is injected as an argument, so this has no
hidden side effect).
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
    """`BT_VECTOR_MONTE_CARLO` — fail-closed rejection for `iterations`<=0,
    `equity_curve` too short, a zero-equity segment, an out-of-range percentile, etc."""


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    """`final_equities[i]` is the final equity of the i-th resampled path (input order =
    iteration order, not sorted). `percentiles` maps the caller-requested percentile
    (an integer 0-100) to the corresponding `final_equities` value."""

    final_equities: tuple[Decimal, ...]
    percentiles: dict[int, Decimal]


def run_monte_carlo(
    base_result: QuickBacktestResult,
    *,
    iterations: int,
    rng: np.random.Generator,
    percentiles: tuple[int, ...] = _DEFAULT_PERCENTILES,
) -> MonteCarloResult:
    """Builds `iterations` paths by bootstrap-reordering the per-bar returns of
    `base_result.equity_curve`, and collects each path's final equity. The starting
    equity is always fixed at `equity_curve[0]` -- the bootstrap only reshuffles the
    order of returns, it never fabricates the equity scale itself."""
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
    """If `curve[i]` is 0, the return from that segment onward (`curve[i+1]/curve[i] -
    1`) is undefined -- a path whose equity was exhausted to 0 is rejected immediately
    rather than silently skipped (fail-closed)."""
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
