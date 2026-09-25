"""Time-Weighted Return (TWR) — "PERIOD_LINKED_CASHFLOW_AT_START" from the `pm-v1` methodology.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.

Splits the period at each boundary with a cash flow and geometrically links sub-period returns
(GIPS-standard TWR, not a Modified Dietz approximation) — therefore an exact valuation must
exist at every cash flow timestamp (`valuations`). When one is missing, explicitly reject
rather than approximate (consistent with the reconciliation "never assume zero" principle).

The valuation at boundary `t_k` is taken **just before** the cash flow at that time is applied
(convention) — the actual investable base for sub-period `[t_{k-1}, t_k]` is
`valuations[t_{k-1}].value + cashflow_at(t_{k-1})` ("cash flow reflected in the base").

The returned value is a ratio (0.0523 = 5.23%), not a percentage number — the `%` display is
the responsibility of the contracts layer (`ReturnValue.value_pct`).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.performance.domain.models import Cashflow, CashflowKind


class MissingInputError(Exception):
    """Error taxonomy `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` — the inputs this function
    requires (valuations at cash flow timestamps, etc.) are missing. Do not fill with zero
    or interpolated values."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"INTEGRITY_STATEMENT_INPUT_UNRECONCILED: {detail}")
        self.reason_code = "INTEGRITY_STATEMENT_INPUT_UNRECONCILED"


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def twr(
    valuations: list[tuple[datetime, Decimal]],
    cashflows: list[Cashflow],
) -> Decimal:
    if len(valuations) < 2:
        raise MissingInputError("twr() requires at least 2 valuations (period start/end).")

    ordered = sorted(valuations, key=lambda v: v[0])
    valuation_times = {at for at, _ in ordered}
    cashflow_by_time: dict[datetime, Decimal] = {}
    for cf in cashflows:
        if cf.at not in valuation_times:
            raise MissingInputError(
                f"No matching valuation at cash flow time {cf.at.isoformat()}."
            )
        cashflow_by_time[cf.at] = cashflow_by_time.get(cf.at, Decimal(0)) + _signed(cf)

    linked = Decimal(1)
    for (prev_at, prev_value), (_cur_at, cur_value) in zip(ordered, ordered[1:], strict=False):
        base = prev_value + cashflow_by_time.get(prev_at, Decimal(0))
        if base == 0:
            raise MissingInputError(
                f"Investable base at {prev_at.isoformat()} is zero — cannot define return."
            )
        subperiod_return = cur_value / base - 1
        linked *= Decimal(1) + subperiod_return

    return linked - 1
