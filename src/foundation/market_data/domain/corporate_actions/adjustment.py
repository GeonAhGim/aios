"""LA-8 — corporate action adjustment coefficient chain, RAW→ADJUSTED candle conversion.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8.

Candles before `ex_date` are adjusted by the cumulative product of all adjustments
that occurred on that date and after — even if the same instrument has 2+ splits,
each candle must reflect only adjustments that occur after its own date, so we
accumulate coefficients in descending `ex_date` order. Decimal multiplication only
(no floats, per spec §9.2 LA-8).

`CorporateAction.ratio` follows the rule "for 2:1 split, ratio=2" (contract docstring)
for SPLIT/REVERSE_SPLIT/MERGER. CASH_DIVIDEND does not mandate `ratio=1` by contract,
so dividend price adjustments (ex-dividend vs. close) are not handled in this leaf —
**unverified**: dividend adjustments require prior-day close, and `factor_chain` does
not receive candles, so we do not reflect dividends in price/volume coefficients (factor=1).
MERGER's exact conversion-ratio convention is also **unverified** and uses the same
formula as SPLIT. No I/O — pure functions only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CandleRecord, CorporateAction

__all__ = [
    "AdjustmentFactor",
    "InvalidActionTypeError",
    "InvalidRatioError",
    "adjust",
    "factor_chain",
]

_ONE = Decimal(1)


class InvalidRatioError(ValueError):
    """Corporate action with `ratio <= 0` received — cannot trust lineage."""

    def __init__(self, action: CorporateAction) -> None:
        super().__init__(
            f"instrument_id={action.instrument_id} ex_date={action.ex_date}: "
            f"ratio={action.ratio} must be positive."
        )
        self.action = action


class InvalidActionTypeError(ValueError):
    """`action_type` is none of the 4 known values (SPLIT/REVERSE_SPLIT/
    CASH_DIVIDEND/MERGER). `CorporateAction.action_type` is a Literal, so
    this cannot happen via normal construction — but a corrupted record
    (e.g. `model_construct` bypassing validation, or a stale value left by
    an earlier, looser schema) could still reach here, so fail closed."""

    def __init__(self, action: CorporateAction) -> None:
        super().__init__(
            f"instrument_id={action.instrument_id} ex_date={action.ex_date}: "
            f"unknown action_type={action.action_type!r}"
        )
        self.action = action


@dataclass(frozen=True, slots=True)
class AdjustmentFactor:
    """Cumulative coefficient to multiply candles strictly before `effective_date`.

    All adjustments from `effective_date` onwards are already accumulated."""

    instrument_id: UUID
    effective_date: date
    price_factor: Decimal
    volume_factor: Decimal


def _action_factors(action: CorporateAction) -> tuple[Decimal, Decimal]:
    """(price factor, volume factor) — values to multiply past candles by.
    For 2:1 split (ratio=2), past prices scale by 1/2, past volumes scale by 2x."""
    if action.ratio <= 0:
        raise InvalidRatioError(action)
    if action.action_type == "REVERSE_SPLIT":
        return action.ratio, _ONE / action.ratio
    if action.action_type in ("SPLIT", "MERGER", "CASH_DIVIDEND"):
        # CASH_DIVIDEND ratio=1 convention -- see module docstring above.
        return _ONE / action.ratio, action.ratio
    raise InvalidActionTypeError(action)


def factor_chain(actions: list[CorporateAction], as_of: datetime) -> list[AdjustmentFactor]:
    """Collect adjustments effective up to `as_of` and return cumulative factors per instrument.

    Returned list is sorted by `ex_date` ascending; each element is the cumulative
    coefficient to apply to candles before that `ex_date` (including itself and all later
    adjustments)."""
    as_of_date = as_of.date()
    by_instrument: dict[UUID, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        if action.ratio <= 0:
            raise InvalidRatioError(action)
        if action.ex_date > as_of_date:
            continue
        by_instrument[action.instrument_id].append(action)

    factors: list[AdjustmentFactor] = []
    for instrument_id, instrument_actions in by_instrument.items():
        ordered = sorted(instrument_actions, key=lambda a: a.ex_date, reverse=True)
        cumulative_price = _ONE
        cumulative_volume = _ONE
        instrument_factors: list[AdjustmentFactor] = []
        for action in ordered:
            price_mult, volume_mult = _action_factors(action)
            cumulative_price *= price_mult
            cumulative_volume *= volume_mult
            instrument_factors.append(
                AdjustmentFactor(
                    instrument_id=instrument_id,
                    effective_date=action.ex_date,
                    price_factor=cumulative_price,
                    volume_factor=cumulative_volume,
                )
            )
        factors.extend(reversed(instrument_factors))
    return sorted(factors, key=lambda f: (f.instrument_id, f.effective_date))


def _factor_for(
    instrument_id: UUID,
    open_time: datetime,
    factors_by_instrument: dict[UUID, list[AdjustmentFactor]],
) -> tuple[Decimal, Decimal]:
    candle_date = open_time.date()
    for factor in factors_by_instrument.get(instrument_id, ()):
        if factor.effective_date > candle_date:
            return factor.price_factor, factor.volume_factor
    return _ONE, _ONE


def adjust(candles: list[CandleRecord], factors: list[AdjustmentFactor]) -> list[CandleRecord]:
    """Apply `factor_chain` results to RAW candles to create ADJUSTED candles.

    To reflect only adjustments that occur after the candle date, use the first
    coefficient (per instrument, in ascending `effective_date` order) that exceeds the candle
    date."""
    by_instrument: dict[UUID, list[AdjustmentFactor]] = defaultdict(list)
    for f in factors:
        by_instrument[f.instrument_id].append(f)
    for instrument_factors in by_instrument.values():
        instrument_factors.sort(key=lambda f: f.effective_date)

    adjusted: list[CandleRecord] = []
    for candle in candles:
        price_factor, volume_factor = _factor_for(
            candle.key.instrument_id, candle.open_time, by_instrument
        )
        if price_factor == _ONE and volume_factor == _ONE:
            adjusted.append(candle)
            continue
        adjusted.append(
            candle.model_copy(
                update={
                    "open": candle.open * price_factor,
                    "high": candle.high * price_factor,
                    "low": candle.low * price_factor,
                    "close": candle.close * price_factor,
                    "volume": candle.volume * volume_factor,
                }
            )
        )
    return adjusted
