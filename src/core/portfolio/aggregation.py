"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 95, §9 L18 — aggregation.py.

8.2-C: Pure functions that aggregate positions and cash by symbol/strategy/total
exposure across multiple executions. `PortfolioAggregate` is already defined in
L17 (`state_input.py`) as the type for the `exposures` field — we import and
use it here rather than redefining it. This file only adds the
`ExecutionExposure` input type and the `aggregate()` function.

`aggregate()` does not accept `total_equity` as an argument — since the public
contract takes only three parameters `(exposures, cash, as_of)`, this function
defines total equity directly as "cash + sum of exposures". All percentages are
computed by dividing each group's exposure sum by that single `total_equity`
value, and `total_exposure_pct`/`cash_pct` are just regroupings of the same
division, so the sum identity holds algebraically without rounding (§9 DoD (b))
— provided the division itself yields a finite decimal (repeating decimals are
rounded at the `Decimal` context precision, default 28 digits, which may make
the identity approximate; this contract does not handle that case).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.portfolio.state_input import PortfolioAggregate

_HUNDRED = Decimal("100")


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float is not accepted here — pass Decimal")
    return value


class AggregationError(ValueError):
    """Aggregation input violated its defined contract — reject here with
    fail-closed instead of leaking as `ZeroDivisionError` or other
    undefined exceptions."""


class MissingAsOfError(AggregationError):
    """`as_of` was passed as `None` — force the caller to provide an explicit
    timestamp rather than arbitrarily defaulting to "now" (reproducibility R1)."""


class NonPositiveEquityError(AggregationError):
    """`cash + Σnotional` (total equity) is zero or negative — reject instead of
    leaking as `ZeroDivisionError` or negative percentages, since there is no
    valid denominator."""


class ExecutionExposure(BaseModel):
    execution_id: int
    strategy_id: str
    symbol: str
    notional: Decimal
    vol_pct: Decimal | None = None

    @field_validator("notional", "vol_pct", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)


def aggregate(
    exposures: Sequence[ExecutionExposure], cash: Decimal, as_of: datetime
) -> PortfolioAggregate:
    """Group `notional` values across multiple executions by symbol/strategy
    and convert to percentages.

    `total_equity = cash + Σ notional`. Group sums by symbol/strategy are
    merely a redistribution of the total exposure sum, so adding all group
    percentages yields `total_exposure_pct`, and `cash_pct + total_exposure_pct`
    equals 100 (see module docstring).
    """
    if as_of is None:
        raise MissingAsOfError("as_of is required — not replaced with a default (current time).")

    total_notional = Decimal("0")
    per_symbol_notional: dict[str, Decimal] = {}
    per_strategy_notional: dict[str, Decimal] = {}
    for exposure in exposures:
        total_notional += exposure.notional
        per_symbol_notional[exposure.symbol] = (
            per_symbol_notional.get(exposure.symbol, Decimal("0")) + exposure.notional
        )
        per_strategy_notional[exposure.strategy_id] = (
            per_strategy_notional.get(exposure.strategy_id, Decimal("0")) + exposure.notional
        )

    total_equity = cash + total_notional
    if total_equity <= 0:
        raise NonPositiveEquityError(
            f"total_equity(cash+total_exposure)={total_equity} is at or below zero — "
            "cannot define percentages, rejecting aggregation."
        )

    def _pct(notional: Decimal) -> Decimal:
        return notional / total_equity * _HUNDRED

    per_symbol_pct = {symbol: _pct(notional) for symbol, notional in per_symbol_notional.items()}
    per_strategy_pct = {
        strategy_id: _pct(notional) for strategy_id, notional in per_strategy_notional.items()
    }

    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct,
        per_strategy_pct=per_strategy_pct,
        total_exposure_pct=_pct(total_notional),
        cash_pct=_pct(cash),
        as_of=as_of,
    )
