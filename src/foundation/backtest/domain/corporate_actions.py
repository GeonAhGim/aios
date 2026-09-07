"""BT-20 — backtest corporate-action (split/cash dividend) adjustment (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.4 (`BacktestConfigV2.adjustments{splits, dividends}`),
docs/design/ADR-2026-09-06-G-second-audit-corrections.md line 135 (BT-20
"corporate actions not reflected in backtest — TradingView/Bloomberg provide
this by default").

`BacktestConfigV2.adjustments` was declared in the contract but no leaf
actually applied it. Splits adjust both price and quantity; dividends adjust
price only (on a total-return basis) — historical prices are rebased to the
current share count to remove the artificial price discontinuity at the
split date, and the price drop from going ex-dividend is corrected to not
read as a real loss.

Unlike the `costs` package's (BT-8) "off = silently 0" convention, here
adjustment being disabled (`config.splits=False`/`config.dividends=False`)
is not a silent pass-through — `AdjustedQuote.applied` /
`AdjustedFill.{splits,dividends}_applied` carry that fact through in the
return value (applied=False). Callers must surface this flag in result
metrics and must not conclude "there was nothing to adjust" from
`adjusted == raw` alone (principle 46 "surface limits/assumptions", BT-20
DoD "unadjusted mode is flagged in result metrics" — unapplied adjustment is
never silently passed through).

`StockSplit.ratio` follows the "how many shares does one share become"
(new/old) convention — a 2:1 split is `ratio=Decimal(2)` (historical price
divided by 2, quantity multiplied by 2). Dividend adjustment uses the CRSP
method (a cumulative product that lowers historical prices by the ratio of
dividend amount to the prior-close before the ex-date) (unverified: no
cross-check against actual per-vendor adjustment algorithms was done). This
module is a pure function that does not query a quote store, so the dividend
event carries the prior-close before the ex-date (`prior_close`) directly —
the caller passes that close along with it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import AdjustmentsConfig

__all__ = [
    "StockSplit",
    "CashDividend",
    "AdjustedQuote",
    "AdjustedFill",
    "split_factor",
    "dividend_factor",
    "adjust_price_for_splits",
    "adjust_quantity_for_splits",
    "adjust_price_for_dividends",
    "adjust_fill",
]


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def _reject_non_positive_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value <= 0:
        raise ValueError(f"{name}는 0 이하·NaN을 허용하지 않는다: {value}")


@dataclass(frozen=True, slots=True)
class StockSplit:
    """One split (or reverse split) event. A 2:1 split is `ratio=Decimal(2)`;
    a 1:2 reverse split is `ratio=Decimal('0.5')`."""

    ex_date: datetime
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class CashDividend:
    """One cash dividend event. `prior_close` is the close before the
    ex-date — this module does not query quotes, so the caller passes it
    directly."""

    ex_date: datetime
    amount: Decimal
    prior_close: Decimal


@dataclass(frozen=True, slots=True)
class AdjustedQuote:
    """Raw value, adjusted value, and whether adjustment was applied for a
    single value (price or quantity).

    If `applied=False`, `adjusted == raw`, but that does not mean the ratio
    happened to be 1 — it means adjustment was turned off by config. The
    caller must surface this field in result metrics as-is."""

    raw: Decimal
    adjusted: Decimal
    applied: bool


@dataclass(frozen=True, slots=True)
class AdjustedFill:
    """Split/dividend adjustment result for one fill (price + quantity).

    The two flags are kept separate because even in a combination where only
    one side is on (e.g. `splits=True, dividends=False`), result metrics
    must be able to distinguish which side is unadjusted."""

    raw_price: Decimal
    raw_quantity: Decimal
    adjusted_price: Decimal
    adjusted_quantity: Decimal
    splits_applied: bool
    dividends_applied: bool


def _require_ordered_window(bar_time: datetime, as_of: datetime) -> None:
    _require_utc(bar_time, "bar_time")
    _require_utc(as_of, "as_of")
    if as_of < bar_time:
        raise ValueError(
            f"as_of는 bar_time보다 앞일 수 없다: bar_time={bar_time}, as_of={as_of}"
        )


def split_factor(
    splits: Sequence[StockSplit], *, bar_time: datetime, as_of: datetime
) -> Decimal:
    """Cumulative multiplier that rebases the raw price at `bar_time` to the
    share count as of `as_of`. Only splits whose ex_date falls in
    (`bar_time`, `as_of`] are reflected — a half-open interval (same
    convention as funding.py's settlement boundary; a split occurring on
    `bar_time` itself is excluded on the assumption that the bar's price is
    already post-split)."""

    _require_ordered_window(bar_time, as_of)
    factor = Decimal(1)
    for index, split in enumerate(splits):
        _require_utc(split.ex_date, f"splits[{index}].ex_date")
        _reject_non_positive_or_nan(split.ratio, f"splits[{index}].ratio")
        if bar_time < split.ex_date <= as_of:
            factor *= split.ratio
    return factor


def dividend_factor(
    dividends: Sequence[CashDividend], *, bar_time: datetime, as_of: datetime
) -> Decimal:
    """Cumulative multiplier that, when multiplied by the raw price at
    `bar_time`, produces the total-return adjusted price as of `as_of` (CRSP
    method: lowers historical prices by the ratio of dividend amount to the
    prior-close before the ex-date). Same half-open interval rule as
    `split_factor`."""

    _require_ordered_window(bar_time, as_of)
    factor = Decimal(1)
    for index, dividend in enumerate(dividends):
        _require_utc(dividend.ex_date, f"dividends[{index}].ex_date")
        _reject_negative_or_nan(dividend.amount, f"dividends[{index}].amount")
        _reject_non_positive_or_nan(dividend.prior_close, f"dividends[{index}].prior_close")
        if bar_time < dividend.ex_date <= as_of:
            factor *= Decimal(1) - dividend.amount / dividend.prior_close
    return factor


def adjust_price_for_splits(
    config: AdjustmentsConfig,
    splits: Sequence[StockSplit],
    *,
    raw_price: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    """If `config.splits=False`, returns raw as-is even after validating the
    other arguments, but explicitly sets `applied=False` (off is never left
    as merely raw==adjusted, silently)."""

    _reject_negative_or_nan(raw_price, "raw_price")
    factor = split_factor(splits, bar_time=bar_time, as_of=as_of)
    if not config.splits:
        return AdjustedQuote(raw=raw_price, adjusted=raw_price, applied=False)
    return AdjustedQuote(raw=raw_price, adjusted=raw_price / factor, applied=True)


def adjust_quantity_for_splits(
    config: AdjustmentsConfig,
    splits: Sequence[StockSplit],
    *,
    raw_quantity: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    _reject_negative_or_nan(raw_quantity, "raw_quantity")
    factor = split_factor(splits, bar_time=bar_time, as_of=as_of)
    if not config.splits:
        return AdjustedQuote(raw=raw_quantity, adjusted=raw_quantity, applied=False)
    return AdjustedQuote(raw=raw_quantity, adjusted=raw_quantity * factor, applied=True)


def adjust_price_for_dividends(
    config: AdjustmentsConfig,
    dividends: Sequence[CashDividend],
    *,
    raw_price: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    _reject_negative_or_nan(raw_price, "raw_price")
    factor = dividend_factor(dividends, bar_time=bar_time, as_of=as_of)
    if not config.dividends:
        return AdjustedQuote(raw=raw_price, adjusted=raw_price, applied=False)
    return AdjustedQuote(raw=raw_price, adjusted=raw_price * factor, applied=True)


def adjust_fill(
    config: AdjustmentsConfig,
    *,
    raw_price: Decimal,
    raw_quantity: Decimal,
    bar_time: datetime,
    as_of: datetime,
    splits: Sequence[StockSplit] = (),
    dividends: Sequence[CashDividend] = (),
) -> AdjustedFill:
    """Applies adjustment to the raw fill price/quantity in split-then-
    dividend order (splits affect both price and quantity so they apply
    first; dividends add a total-return adjustment to price only — the
    dividend multiplier is applied on top of the already-split-adjusted
    price)."""

    price_after_splits = adjust_price_for_splits(
        config, splits, raw_price=raw_price, bar_time=bar_time, as_of=as_of
    )
    quantity_after_splits = adjust_quantity_for_splits(
        config, splits, raw_quantity=raw_quantity, bar_time=bar_time, as_of=as_of
    )
    price_after_dividends = adjust_price_for_dividends(
        config,
        dividends,
        raw_price=price_after_splits.adjusted,
        bar_time=bar_time,
        as_of=as_of,
    )
    return AdjustedFill(
        raw_price=raw_price,
        raw_quantity=raw_quantity,
        adjusted_price=price_after_dividends.adjusted,
        adjusted_quantity=quantity_after_splits.adjusted,
        splits_applied=price_after_splits.applied,
        dividends_applied=price_after_dividends.applied,
    )
