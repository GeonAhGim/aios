"""Money-Weighted Rate of Return (MWR) — deterministically solves IRR via bisection.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6, methodology.py
(`MWR_MAX_ITERATIONS=200`, `MWR_TOLERANCE=1e-10`).

Converts to a cash-flow stream from the investor's perspective — the
`start_value` at period start is money the investor "puts in" (outflow),
deposits during the period are additional outflows, withdrawals are
inflows, and the `end_value` at period end is money the investor
"can withdraw" (inflow). The discount rate r that makes the net present
value of this stream zero is the IRR. Time is normalised to [0, 1]
(period length = 1); annualisation is the caller's responsibility
(risk_metrics.py consumer).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from src.foundation.performance.domain.methodology import MWR_MAX_ITERATIONS, MWR_TOLERANCE
from src.foundation.performance.domain.models import Cashflow, CashflowKind

_LOW_RATE = Decimal("-0.999999")
_HIGH_RATE = Decimal(10)


def _signed(cf: Cashflow) -> Decimal:
    return cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount


def _npv(rate: Decimal, flows: Sequence[tuple[Decimal, Decimal]]) -> Decimal:
    total = Decimal(0)
    base = Decimal(1) + rate
    for t, amount in flows:
        discount = base**t if t != 0 else Decimal(1)
        total += amount / discount
    return total


def mwr(
    cashflows: Sequence[Cashflow],
    start_value: Decimal,
    end_value: Decimal,
    start: datetime,
    end: datetime,
) -> Decimal | None:
    total_seconds = (end - start).total_seconds()
    if total_seconds <= 0 or start_value <= 0:
        return None

    flows: list[tuple[Decimal, Decimal]] = [(Decimal(0), -start_value)]
    for cf in cashflows:
        elapsed = (cf.at - start).total_seconds()
        if elapsed < 0 or elapsed > total_seconds:
            # cash flows outside the period are the caller's responsibility — silently skip
            continue
        t = Decimal(elapsed) / Decimal(total_seconds)
        flows.append((t, -_signed(cf)))
    flows.append((Decimal(1), end_value))

    low, high = _LOW_RATE, _HIGH_RATE
    f_low, f_high = _npv(low, flows), _npv(high, flows)
    if f_low == 0:
        return low
    if f_high == 0:
        return high
    if (f_low > 0) == (f_high > 0):
        return None  # sign does not change within the bracket — convergence failed

    for _ in range(MWR_MAX_ITERATIONS):
        mid = (low + high) / 2
        f_mid = _npv(mid, flows)
        if abs(f_mid) < MWR_TOLERANCE or (high - low) < MWR_TOLERANCE:
            return mid
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return None  # did not converge within MWR_MAX_ITERATIONS
