"""BT-8 — Perpetual futures funding-rate model (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8, §3.4(`costs: {funding: bool, borrow_apr}`),
§9.5 BT-8(DoD: "accurate pro-rata calculation").

Perpetual futures funding is assumed to settle at a fixed interval
(default 8 hours), at timestamps that are multiples of that interval
relative to the UNIX epoch (1970-01-01T00:00:00Z). This follows the
00:00/08:00/16:00 UTC convention used by major exchanges such as
Binance and Bybit — unverified: no per-exchange spec cross-check was
performed. Exchanges using different settlement times can be
corrected via `interval_hours`.

Sign convention (standard for perpetual futures): if `funding_rate > 0`,
longs pay shorts. The return value is the net cost from the position
holder's perspective — positive means a payment (cost), negative means
a receipt (income). `side=BUY` means a long position, `side=SELL` means
a short position (this refers to the held position direction, not the
order direction).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.costs import exact_total_seconds, round_cost
from src.foundation.backtest.domain.models_v2 import CostsConfig

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DEFAULT_INTERVAL_HOURS = 8


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def _ceil_div(numerator: Decimal, denominator: Decimal) -> int:
    quotient, remainder = divmod(numerator, denominator)
    if remainder != 0:
        quotient += 1
    return int(quotient)


def count_funding_settlements(
    *, entry_time: datetime, exit_time: datetime, interval_hours: int = _DEFAULT_INTERVAL_HOURS
) -> int:
    """Number of settlement timestamps within the half-open interval `[entry_time, exit_time)`.

    If entry falls exactly on a settlement timestamp, that settlement is
    included (held from that instant); if exit falls exactly on a
    settlement timestamp, that settlement is excluded (position ends at
    that instant — assumes the next trade picks it up, preventing
    double-counting at the boundary).
    """

    _require_utc(entry_time, "entry_time")
    _require_utc(exit_time, "exit_time")
    if interval_hours <= 0:
        raise ValueError(f"interval_hours는 양수여야 한다: {interval_hours}")
    if exit_time < entry_time:
        raise ValueError(
            f"exit_time은 entry_time보다 앞일 수 없다: entry={entry_time}, exit={exit_time}"
        )

    interval_seconds = Decimal(interval_hours) * Decimal(3600)
    entry_offset = exact_total_seconds(entry_time - _EPOCH)
    exit_offset = exact_total_seconds(exit_time - _EPOCH)
    first_n = _ceil_div(entry_offset, interval_seconds)
    last_n_exclusive = _ceil_div(exit_offset, interval_seconds)
    return max(0, last_n_exclusive - first_n)


def compute_funding_cost(
    config: CostsConfig,
    *,
    side: OrderSide,
    notional: Decimal,
    funding_rate: Decimal,
    entry_time: datetime,
    exit_time: datetime,
    interval_hours: int = _DEFAULT_INTERVAL_HOURS,
) -> Decimal:
    """Net funding cost settled during the holding period (payment positive / receipt negative).

    If `config.funding=False`, returns `Decimal('0')` immediately without
    validating the other arguments (a disabled cost is zero cost, not an
    exception).
    """

    if not config.funding:
        return Decimal("0")

    _reject_negative_or_nan(notional, "notional")
    if funding_rate.is_nan():
        raise ValueError(f"funding_rate는 NaN을 허용하지 않는다: {funding_rate}")

    settlements = count_funding_settlements(
        entry_time=entry_time, exit_time=exit_time, interval_hours=interval_hours
    )
    direction = Decimal(1) if side == OrderSide.BUY else Decimal(-1)
    cost = notional * funding_rate * direction * settlements
    return round_cost(cost)
