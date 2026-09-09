"""DC-25: deterministic daily close series with backward roll adjustment.

Roll dates exclude the expiry trading day when counting a positive offset.
Offset zero uses the expiry day or its preceding session. On a roll date,
the incoming contract is selected; both same-day closes anchor adjustment.
These are caller-selected rules, not exchange-specific expiry conventions.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, DecimalException, localcontext
from enum import Enum

from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentKind
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar


class RollError(ValueError):
    """Invalid contracts, calendar coverage, or prices prevent construction."""


class Adjustment(str, Enum):
    NONE = "NONE"
    RATIO = "RATIO"
    DIFFERENCE = "DIFFERENCE"


@dataclass(frozen=True)
class CloseBar:
    day: date
    close: Decimal
    instrument_id: str


def roll_date(
    expiry: datetime, calendar: VenueCalendar, offset_trading_days: int = 5,
) -> date:
    """Count actual sessions backwards, with a bounded calendar search."""
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        raise RollError("Expiry must be timezone-aware")
    if type(offset_trading_days) is not int or not 0 <= offset_trading_days <= 366:
        raise RollError("Trading-day offset must be an integer in [0, 366]")
    day = calendar.trading_day_of(expiry)
    remaining = offset_trading_days
    for _ in range(3660):
        if remaining == 0 and calendar.sessions_for(day):
            return day
        if day == date.min:
            break
        day -= timedelta(days=1)
        if remaining and calendar.sessions_for(day):
            remaining -= 1
    raise RollError("Calendar has insufficient sessions before expiry")


def _contracts(contracts: Sequence[Instrument], calendar: VenueCalendar) -> None:
    if not contracts:
        raise RollError("At least one futures contract is required")
    first = contracts[0]
    previous: datetime | None = None
    ids: set[str] = set()
    for contract in contracts:
        if contract.kind != InstrumentKind.FUTURE or contract.expiry is None:
            raise RollError("Each contract must be FUTURE with an expiry")
        if previous is not None and contract.expiry <= previous:
            raise RollError("Expiries must be strictly increasing")
        multiplier = contract.contract_multiplier
        if (contract.underlying_id is None or multiplier is None
                or not multiplier.is_finite() or multiplier <= 0):
            raise RollError("Underlying and positive finite multiplier are required")
        if (contract.underlying_id != first.underlying_id
                or multiplier != first.contract_multiplier
                or contract.calendar_id != calendar.venue):
            raise RollError("Contracts must share underlying, multiplier, and calendar")
        if contract.instrument_id in ids:
            raise RollError("Contract identifiers must be unique")
        ids.add(contract.instrument_id)
        previous = contract.expiry


def continuous_futures(
    contracts: Sequence[Instrument],
    closes: Mapping[str, Mapping[date, Decimal]],
    days: Sequence[date],
    calendar: VenueCalendar,
    offset_trading_days: int = 5,
    adjustment: Adjustment = Adjustment.RATIO,
) -> tuple[CloseBar, ...]:
    """Build daily closes on explicit ascending session dates, without filling gaps.

Only rolls within the requested history are applied. The final contract is
usable through its expiry trading day; later dates require another contract.
Decimal arithmetic uses a fixed 38-digit context, independent of the caller.
"""
    _contracts(contracts, calendar)
    if not isinstance(adjustment, Adjustment):
        raise RollError("Unknown adjustment method")
    if not days or any(a >= b for a, b in zip(days, days[1:], strict=False)):
        raise RollError("Requested dates must be nonempty and strictly increasing")
    expiries = [c.expiry for c in contracts if c.expiry is not None]
    rolls = [roll_date(e, calendar, offset_trading_days) for e in expiries]
    if any(a >= b for a, b in zip(rolls, rolls[1:], strict=False)):
        raise RollError("Roll dates must be strictly increasing")
    if days[-1] > calendar.trading_day_of(expiries[-1]):
        raise RollError("History extends beyond the final contract expiry")

    def price(index: int, day: date) -> Decimal:
        value = closes.get(contracts[index].instrument_id, {}).get(day)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise RollError(f"Missing or invalid close for contract {index} on {day}")
        return value

    result: list[CloseBar] = []
    active = 0
    try:
        with localcontext(Context(prec=38)):
            for day in days:
                if not calendar.sessions_for(day):
                    raise RollError("Requested date is not a trading session")
                while active < len(contracts) - 1 and day >= rolls[active]:
                    boundary = rolls[active]
                    if result and adjustment != Adjustment.NONE:
                        near, far = price(active, boundary), price(active + 1, boundary)
                        if adjustment == Adjustment.RATIO:
                            if near <= 0 or far <= 0:
                                raise RollError("Ratio adjustment requires positive roll closes")
                            factor, shift = far / near, Decimal(0)
                        else:
                            factor, shift = Decimal(1), far - near
                        result = [CloseBar(b.day, b.close * factor + shift, b.instrument_id)
                                  for b in result]
                    active += 1
                result.append(CloseBar(day, price(active, day), contracts[active].instrument_id))
    except DecimalException as exc:
        raise RollError("Adjustment exceeds Decimal arithmetic limits") from exc
    return tuple(result)
