"""BT-12 — backtest performance report view.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 `application/tearsheet.py` (performance report, reuses the existing
performance contract), §9.5 BT-12 (prerequisite BT-10).

Does not reimplement the metric formulas (Sharpe/Sortino/MDD/win
rate/return) — it only reads `BacktestResult.metrics` as already computed by
BT-10 (`compute_metrics.py`) and repacks it into the `ReturnValue` DTO shape
from `src/foundation/performance/contracts/v1.py` (prefer vendor/existing
context principle, §C).

A pure function — no I/O, clock, or randomness access. The same
`BacktestResult` input always yields a byte-identical `TearsheetView` (leaf
DoD "report snapshot determinism"). HTTP routing and DB storage (report
persistence) are out of scope for this leaf (a follow-up leaf).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import SCHEMA_VERSION, ReturnValue

__all__ = ["TearsheetView", "build_tearsheet"]


class TearsheetView(BaseModel):
    """BT-12 report snapshot. `Decimal` fields are serialized to strings by
    default under pydantic v2 JSON mode (`model_dump(mode="json")`) — no
    separate encoder is needed."""

    strategy_id: str
    strategy_version: str
    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    final_equity: Decimal
    total_return: ReturnValue
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    win_rate_pct: Decimal | None
    total_trades: int
    turnover: Decimal
    basis: Literal["PAPER_SIM"]
    config_hash: str
    limitations: list[str]
    schema_version: str = SCHEMA_VERSION


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """`BacktestResult` (run_backtest.py's output, `metrics` already computed
    by BT-10) -> report view. An empty `equity_curve` signals a caller bug,
    so this rejects it rather than silently filling in 0 (same principle as
    compute_metrics.py)."""
    if not result.equity_curve:
        raise ValueError("빈 equity_curve로는 리포트를 만들 수 없습니다.")

    metrics = result.metrics
    config = result.config
    total_return = ReturnValue(
        value_pct=metrics.total_return_pct,
        basis="NET",  # equity basis already reflects fill fee/slippage in fills (row 76 §2)
        method="TWR",
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        annualized=False,
        periods_per_year=config.periods_per_year,
    )
    return TearsheetView(
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        period_start=metrics.period_start,
        period_end=metrics.period_end,
        initial_equity=config.initial_equity,
        final_equity=result.equity_curve[-1].equity,
        total_return=total_return,
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        win_rate_pct=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        basis=metrics.basis,
        config_hash=config.config_hash(),
        limitations=list(result.warnings),
    )
