"""LB-4 — Currency conversion (fx).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-4.

Converts `Money` to the base currency. Raises explicit exceptions (no silent
fallback or 0 substitution) when the exchange rate is missing or stale —
reuses `POS_FX_RATE_MISSING` from `PositionErrorCode` as-is (§3.2 error
taxonomy: "possible, wait for rate arrival — do not substitute 0").
Triangular conversion is prohibited: if the provided `FXRate` does not
directly express the `(m.currency, to)` pair (base→quote) or in reverse
(quote→base), treat it as missing — the caller must look up chained rates
in advance. Pure function only — no I/O or clock calls; staleness judgment
is passed by the caller via the `now` argument.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.contracts.v1 import PositionErrorCode

DEFAULT_MAX_RATE_AGE = timedelta(minutes=5)


class FxRateMissingError(Exception):
    """`POS_FX_RATE_MISSING` — No exchange rate for the requested currency pair
    (retryable, after rate arrives). Does not substitute 0."""

    code = PositionErrorCode.FX_RATE_MISSING

    def __init__(self, source: Currency, target: Currency, *, message: str | None = None) -> None:
        super().__init__(message or f"{source.value}->{target.value}: 사용 가능한 환율이 없습니다.")
        self.source = source
        self.target = target


class FxRateStaleError(FxRateMissingError):
    """Same retry class as `POS_FX_RATE_MISSING` — the rate exists but is
    older than `max_age`. Does not silently continue with a stale rate."""

    def __init__(self, rate: FXRate, *, now: datetime, max_age: timedelta) -> None:
        age = now - rate.timestamp
        super().__init__(
            rate.base,
            rate.quote,
            message=(
                f"{rate.base.value}->{rate.quote.value}: 환율이 스테일합니다"
                f"(age={age}, max_age={max_age}, source={rate.source})."
            ),
        )
        self.age = age
        self.max_age = max_age
        self.rate = rate


@dataclass(frozen=True, slots=True)
class Converted:
    """Conversion result. `rate` is filled only when the currency actually
    changed (same currency: `rate=None`) — satisfies the §3.2 requirement to
    include the rate source and timestamp in the result."""

    amount: Decimal
    currency: Currency
    rate: FXRate | None


def convert(
    m: Money,
    to: Currency,
    rate: FXRate | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_RATE_AGE,
) -> Converted:
    """Convert `m` to `to` currency.

    - If `m.currency == to`, returns the amount unchanged (no rate lookup).
    - Otherwise `rate` must be present, and `(rate.base, rate.quote)` must
      exactly match `(m.currency, to)` (direct) or `(to, m.currency)`
      (reverse). Any other pair (i.e., triangular conversion needed) is
      treated as missing.
    - If `now` is provided, raises `FxRateStaleError` when
      `rate.timestamp` is older than `max_age` (skips staleness check when
      `now` is omitted — for pure computation contexts where the caller
      does not hold a clock).
    """
    if m.currency == to:
        return Converted(amount=m.amount, currency=to, rate=None)

    if rate is None:
        raise FxRateMissingError(m.currency, to)

    if now is not None and (now - rate.timestamp) > max_age:
        raise FxRateStaleError(rate, now=now, max_age=max_age)

    if rate.base == m.currency and rate.quote == to:
        amount = m.amount * rate.rate
    elif rate.base == to and rate.quote == m.currency:
        if rate.rate == 0:
            raise FxRateMissingError(m.currency, to)
        amount = m.amount / rate.rate
    else:
        raise FxRateMissingError(m.currency, to)

    return Converted(amount=amount, currency=to, rate=rate)
