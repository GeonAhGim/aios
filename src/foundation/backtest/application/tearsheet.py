"""BT-12 performance report assembly for completed backtests."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer

from src.foundation.backtest.application.compute_metrics import compute_metrics
from src.foundation.backtest.domain.models import BacktestResult
from src.foundation.performance.contracts.v1 import ReturnValue

_PERCENT = Decimal("100")


class EmptyEquityCurveError(ValueError):
    """Raised when a report has no equity observations."""


class TearsheetView(BaseModel):
    """Stable v1 snapshot of backtest performance metrics."""

    period_start: datetime
    period_end: datetime
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    max_drawdown: Decimal
    sharpe: Decimal | None
    sortino: Decimal | None
    win_rate: Decimal | None
    total_trades: int
    turnover: Decimal
    returns: list[ReturnValue]
    risk: dict[str, Decimal | None]
    warnings: list[str]
    schema_version: Literal["v1"] = "v1"

    @field_serializer(
        "initial_equity",
        "final_equity",
        "total_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "win_rate",
        "turnover",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @field_serializer("risk", when_used="json")
    def _serialize_risk(self, value: dict[str, Decimal | None]) -> dict[str, str | None]:
        return {key: None if metric is None else str(metric) for key, metric in value.items()}


def build_tearsheet(result: BacktestResult) -> TearsheetView:
    """Build a deterministic report without reimplementing metric formulas."""
    if not result.equity_curve:
        raise EmptyEquityCurveError("A tearsheet requires at least one equity point.")

    metrics = compute_metrics(
        equity_curve=result.equity_curve,
        fills=result.fills,
        initial_equity=result.config.initial_equity,
        periods_per_year=result.config.periods_per_year,
    )
    period_start = metrics.period_start
    period_end = metrics.period_end
    total_return = metrics.total_return_pct / _PERCENT
    max_drawdown = metrics.max_drawdown_pct / _PERCENT
    return TearsheetView(
        period_start=period_start,
        period_end=period_end,
        initial_equity=result.config.initial_equity,
        final_equity=result.equity_curve[-1].equity,
        total_return=total_return,
        max_drawdown=max_drawdown,
        sharpe=metrics.sharpe_ratio,
        sortino=metrics.sortino_ratio,
        win_rate=metrics.win_rate_pct,
        total_trades=metrics.total_trades,
        turnover=metrics.turnover,
        returns=[
            ReturnValue(
                value_pct=metrics.total_return_pct,
                basis="GROSS",
                method="TWR",
                period_start=period_start,
                period_end=period_end,
                annualized=False,
                periods_per_year=None,
            )
        ],
        risk={
            "max_drawdown": max_drawdown,
            "sharpe": metrics.sharpe_ratio,
            "sortino": metrics.sortino_ratio,
            "win_rate": metrics.win_rate_pct,
        },
        warnings=list(result.warnings),
    )


__all__ = ["EmptyEquityCurveError", "TearsheetView", "build_tearsheet"]
