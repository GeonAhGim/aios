"""BT-8 — Borrowing (short) cost model (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8, §3.4(`costs.borrow_apr: Decimal | None`), §9.5 BT-8(DoD: "Day-count accrual accuracy").

The day-count convention is fixed to ACT/365 (actual elapsed days / 365).
Unlike ACT/365.25 which adjusts the denominator to the actual year length
(leap year 366), or ACT/360 used in bond markets, crypto and equity short-
selling stock-borrow interest commonly follows the industry practice of fixing
the denominator at 365 days — this observation is the adoption rationale
(unverified: we have not cross-checked actual stock-borrow agreements against
each exchange and prime broker). The numerator (actual elapsed days) counts
calendar days as-is even in leap years — only the denominator is fixed at 365
(ACT/365 fixed).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.backtest.domain.costs import exact_total_seconds, round_cost
from src.foundation.backtest.domain.models_v2 import CostsConfig

_DAYS_PER_YEAR = Decimal(365)
_SECONDS_PER_DAY = Decimal(86400)


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a tz-aware UTC datetime: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(
            f"{name} must be non-negative: NaN and negative values are rejected: {value}"
        )


def compute_borrow_cost(
    config: CostsConfig, *, notional: Decimal, entry_time: datetime, exit_time: datetime
) -> Decimal:
    """Borrowing cost based on actual holding days (ACT/365).

    When `config.borrow_apr=None`, returns `Decimal('0')` immediately without
    validating other arguments (no-borrow strategies are not exceptions — they
    are cost-free).
    """

    if config.borrow_apr is None:
        return Decimal("0")

    _require_utc(entry_time, "entry_time")
    _require_utc(exit_time, "exit_time")
    _reject_negative_or_nan(notional, "notional")
    if exit_time < entry_time:
        raise ValueError(
            f"exit_time은 entry_time보다 앞일 수 없다: entry={entry_time}, exit={exit_time}"
        )

    holding_days = exact_total_seconds(exit_time - entry_time) / _SECONDS_PER_DAY
    cost = notional * config.borrow_apr * holding_days / _DAYS_PER_YEAR
    return round_cost(cost)
