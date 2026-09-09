"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 95, §9 L18 — aggregation.py.

8.2-C: a pure function that aggregates positions/cash across multiple
executions into per-symbol/per-strategy/total exposure. `PortfolioAggregate`
is already defined over in L17 (`state_input.py`), which needed it first as
the type of its `exposures` field — it is not redefined here, just imported
and used as-is. The only things this file newly adds are the
`ExecutionExposure` input type and `aggregate()`.

`aggregate()` does not take `total_equity` as an argument — since the public
contract has exactly three arguments, `(exposures, cash, as_of)`, this
function itself defines total equity as "cash + sum of exposures". Every
percentage is that group's exposure sum divided by that same
`total_equity`, and `total_exposure_pct`/`cash_pct` are just recombinations
of the same division, so — without rounding (§9 DoD (b)) — the summation
identity holds algebraically. This assumes, however, that the division
itself lands on inputs that resolve exactly to finite decimals (inputs that
would produce a repeating decimal get rounded at the `Decimal` context's
precision (28 digits by default), which can make the identity only
approximate — this contract does not cover that case).
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
    """8.2-C aggregation input fell outside the defined contract — rejected
    here fail-closed so it does not leak out as an undefined exception like
    `ZeroDivisionError`."""


class MissingAsOfError(AggregationError):
    """`as_of` was passed in as `None` — instead of arbitrarily substituting
    "now", the caller is forced to pass the point in time explicitly
    (reproducibility R1)."""


class NonPositiveEquityError(AggregationError):
    """`cash + Σnotional` (total equity) is zero or negative — the percentage
    denominator is missing or negative, so this is rejected instead of
    leaking out as a `ZeroDivisionError` or a negative percentage."""


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
    """Groups the `notional` of multiple executions by symbol/strategy and
    converts to percentages.

    `total_equity = cash + Σ notional`. The per-symbol/per-strategy group
    sums are just a redistribution of the total exposure sum, so summing
    each group's percentage equals `total_exposure_pct`, and
    `cash_pct + total_exposure_pct` also equals 100 (see module docstring).
    """
    if as_of is None:
        raise MissingAsOfError("as_of는 필수입니다 — 기본값(현재 시각)으로 대체하지 않습니다.")

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
            f"total_equity(cash+총노출)={total_equity}는 0 이하입니다 — "
            "퍼센트를 정의할 수 없어 집계를 거부합니다."
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
