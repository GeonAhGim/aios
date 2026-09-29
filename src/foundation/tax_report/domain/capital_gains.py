"""U-9: aggregate already converted gains, without I/O or tax-rate rules.

Dates are inclusive UTC calendar dates. Currency denotes the base currency
of realized_pnl_base; included entries must share it. Journal lookup, venue
classification and FX conversion belong to the subsequent mapping leaf.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import MAX_EMAX, MIN_EMIN, Context, Decimal, localcontext
from enum import Enum

from src.data.models.base import Currency


class AssetClass(str, Enum):
    DOMESTIC_STOCK = "DOMESTIC_STOCK"
    FOREIGN_STOCK = "FOREIGN_STOCK"
    CRYPTO = "CRYPTO"


@dataclass(frozen=True)
class RealizedGainEntry:
    asset_class: AssetClass
    realized_pnl_base: Decimal
    currency: Currency
    closed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass")
        if not isinstance(self.realized_pnl_base, Decimal):
            raise TypeError("realized_pnl_base must be a Decimal")
        if not self.realized_pnl_base.is_finite():
            raise ValueError("realized_pnl_base must be finite")
        if not isinstance(self.currency, Currency):
            raise TypeError("currency must be a Currency")
        if not isinstance(self.closed_at, datetime):
            raise TypeError("closed_at must be a datetime")
        if self.closed_at.utcoffset() != timedelta(0):
            raise ValueError("closed_at must be timezone-aware UTC")


@dataclass(frozen=True)
class CapitalGainsSummary:
    period_start: date
    period_end: date
    by_asset_class: dict[AssetClass, Decimal]
    total: Decimal


def summarize_realized_gains(
    entries: Sequence[RealizedGainEntry],
    period_start: date,
    period_end: date,
) -> CapitalGainsSummary:
    """Sum finite gains exactly, independently of the caller's Decimal precision.

    Validate even out-of-period records rather than silently hide malformed data.
    No input is mutated and no rounding or quantization is applied. Consumers
    re-summing unusually wide results must also provide sufficient precision.
    """
    if type(period_start) is not date or type(period_end) is not date:
        raise TypeError("period bounds must be dates, not datetimes")
    if period_start > period_end:
        raise ValueError("period_start must not follow period_end")

    included: list[RealizedGainEntry] = []
    for entry in entries:
        if not isinstance(entry, RealizedGainEntry):
            raise TypeError("entries must contain RealizedGainEntry values")
        entry.__post_init__()
        if period_start <= entry.closed_at.date() <= period_end:
            included.append(entry)
    if len({entry.currency for entry in included}) > 1:
        raise ValueError("included gains must share one base currency")

    totals = dict.fromkeys(AssetClass, Decimal("0"))
    if not included:
        return CapitalGainsSummary(period_start, period_end, totals, Decimal("0"))

    # Align the lowest decimal place through the largest integer place, reserving
    # carry digits for every input. This also preserves cancellation of large gains.
    amounts = [entry.realized_pnl_base for entry in included]
    lowest = min(int(amount.as_tuple().exponent) for amount in amounts)
    highest = max(amount.adjusted() for amount in amounts)
    precision = max(1, highest - min(lowest, 0) + len(str(len(amounts))) + 2)
    with localcontext(Context(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN)):
        for entry in included:
            totals[entry.asset_class] += entry.realized_pnl_base
        total = sum(totals.values(), Decimal("0"))
    return CapitalGainsSummary(period_start, period_end, totals, total)
