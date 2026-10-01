"""BT-8 — Backtest cost functions (funding and borrow).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8(`domain/costs/{funding,borrow}.py`), §3.4(`BacktestConfigV2.costs`),
§9.5 BT-8(DoD: "pro-rata calculation accuracy").

`funding.py` (fixed-interval settlement) and `borrow.py` (pro-rata/day-count)
use different settlement logic and live in separate files, but both must
round their final cost through this single `round_cost` — if each module
implements its own rounding, the last digits can diverge for identical
inputs.

DEEPEN task-3039: `exact_total_seconds` also lives here for the same reason.
`timedelta.total_seconds()` returns a `float`, silently breaking this
package's "day-count math stays in Decimal" invariant — for offsets far
from the epoch (e.g. year 2300+) the float division loses precision below
1e-8s, and near a settlement boundary that error could flip the ceiling
division that counts funding settlements.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

_COST_QUANTIZE_EXPONENT = Decimal(
    "0.00000001"
)  # 1e-8 (8 decimal places) — exchange currency precision ceiling convention
_SECONDS_PER_DAY = Decimal(86400)
_MICROSECONDS_PER_SECOND = Decimal(1_000_000)


def round_cost(value: Decimal) -> Decimal:
    """Round a cost value to 8 decimal places using banker's rounding (HALF_EVEN).

    Both `funding.py` and `borrow.py` must pass their final cost through
    this single function before returning, preventing duplicated rounding
    logic across modules.
    """

    return value.quantize(_COST_QUANTIZE_EXPONENT, rounding=ROUND_HALF_EVEN)


def exact_total_seconds(delta: timedelta) -> Decimal:
    """Convert a `timedelta` to an exact `Decimal` count of seconds.

    `delta.total_seconds()` returns a `float`; this instead combines the
    three integer fields (`days`, `seconds`, `microseconds`) directly into a
    `Decimal`, bypassing the float division entirely. `delta` must be
    non-negative (callers already enforce `exit >= entry`).
    """

    return (
        Decimal(delta.days) * _SECONDS_PER_DAY
        + Decimal(delta.seconds)
        + Decimal(delta.microseconds) / _MICROSECONDS_PER_SECOND
    )
