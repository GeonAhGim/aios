"""DC-25 exact adjustment, calendar delegation, and fail-closed evidence."""
from datetime import date, datetime, time, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.foundation.market_data.contracts.v2.instruments import Instrument
from src.foundation.market_data.domain.calendar.session_rules import SessionSpec, VenueCalendar
from src.foundation.market_data.domain.instruments import roll
from src.foundation.market_data.domain.instruments.roll import (
    Adjustment,
    RollError,
    continuous_futures,
    roll_date,
)

D = Decimal


@pytest.fixture
def calendar() -> VenueCalendar:
    tz = ZoneInfo("UTC")
    return VenueCalendar("TEST", tz, SessionSpec(tz, time(9), time(16), frozenset(range(5))),
                         frozenset({date(2026, 9, 11)}))


def contract(index: int, expiry_day: int = 18) -> Instrument:
    return Instrument.model_validate({
        "instrument_id": f"01ARZ3NDEKTSV4RRFFQ69G5FA{index}",
        "asset_class": "CRYPTO", "base": "BTC", "quote": "USD", "isin": None,
        "figi": None, "tick_size": D("0.01"), "lot_size": D(1),
        "calendar_id": "TEST", "lifecycle_state": "ACTIVE",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "kind": "FUTURE",
        "underlying_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "expiry": datetime(2026, 9, expiry_day, 16, tzinfo=timezone.utc),
        "contract_multiplier": D(1),
    })


def test_roll_date_deterministic_and_holiday(calendar: VenueCalendar) -> None:
    expiry = datetime(2026, 9, 18, 16, tzinfo=timezone.utc)
    assert {roll_date(expiry, calendar, 5) for _ in range(100)} == {date(2026, 9, 10)}
    assert roll_date(datetime(2026, 9, 11, tzinfo=timezone.utc), calendar, 0) == date(2026, 9, 10)


@pytest.mark.parametrize("method,expected", [
    (Adjustment.NONE, D(80)), (Adjustment.RATIO, D(84)), (Adjustment.DIFFERENCE, D(85)),
])
def test_exact_adjustment(calendar: VenueCalendar, method: Adjustment, expected: Decimal) -> None:
    near, far = contract(0), contract(1, 25)
    before, boundary, after = date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 14)
    prices = {near.instrument_id: {before: D(80), boundary: D(100)},
              far.instrument_id: {boundary: D(105), after: D(110)}}
    result = continuous_futures([near, far], prices, [before, boundary, after], calendar,
                                adjustment=method)
    assert [b.close for b in result] == [expected, D(105), D(110)]
    assert result[1].instrument_id == far.instrument_id
    assert prices[near.instrument_id][before] == D(80)
    with localcontext() as ctx:
        ctx.prec = 2
        assert continuous_futures([near, far], prices, [before, boundary, after], calendar,
                                  adjustment=method) == result


@pytest.mark.parametrize("changes", [
    {"kind": "SPOT"}, {"kind": None}, {"expiry": None}, {"underlying_id": None},
    {"contract_multiplier": D(0)}, {"calendar_id": "OTHER"},
])
def test_invalid_contract_rejected(calendar: VenueCalendar, changes: dict[str, object]) -> None:
    bad = contract(0).model_copy(update=changes)
    with pytest.raises(RollError):
        continuous_futures([bad], {}, [date(2026, 9, 9)], calendar)


@pytest.mark.parametrize("expiries", [(25, 18), (18, 18)])
def test_expiry_order_rejected(calendar: VenueCalendar, expiries: tuple[int, int]) -> None:
    with pytest.raises(RollError, match="strictly increasing"):
        continuous_futures([contract(i, e) for i, e in enumerate(expiries)], {},
                           [date(2026, 9, 9)], calendar)


@pytest.mark.parametrize("offset", [-1, 367, True])
def test_invalid_offset(calendar: VenueCalendar, offset: int) -> None:
    with pytest.raises(RollError):
        roll_date(datetime(2026, 9, 18, tzinfo=timezone.utc), calendar, offset)


def test_calendar_exhaustion_and_naive_expiry(calendar: VenueCalendar) -> None:
    empty = VenueCalendar("TEST", calendar.tz,
                          SessionSpec(calendar.tz, time(9), time(16), frozenset()))
    with pytest.raises(RollError, match="insufficient"):
        roll_date(datetime(2026, 9, 18, tzinfo=timezone.utc), empty)
    with pytest.raises(RollError, match="timezone"):
        roll_date(datetime(2026, 9, 18), calendar)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), 1.5, None])
def test_bad_prices(calendar: VenueCalendar, bad: Decimal) -> None:
    c = contract(0)
    day = date(2026, 9, 9)
    with pytest.raises(RollError):
        continuous_futures([c], {c.instrument_id: {day: bad}}, [day], calendar)


@pytest.mark.parametrize("near_price", [D(0), D(-100)])
def test_ratio_rejects_nonpositive_anchor(calendar: VenueCalendar, near_price: Decimal) -> None:
    a, b = contract(0), contract(1, 25)
    d1, d2 = date(2026, 9, 9), date(2026, 9, 10)
    prices = {a.instrument_id: {d1: D(80), d2: near_price}, b.instrument_id: {d2: D(105)}}
    with pytest.raises(RollError, match="positive"):
        continuous_futures([a, b], prices, [d1, d2], calendar)


def test_multiple_rolls_and_missing_anchor(calendar: VenueCalendar) -> None:
    a, b, c = contract(0, 10), contract(1, 18), contract(2, 25)
    d1, d2, d3 = date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 18)
    prices = {a.instrument_id: {d1: D(80), d2: D(100)},
              b.instrument_id: {d2: D(105), d3: D(100)}, c.instrument_id: {d3: D(110)}}
    assert [b.close for b in continuous_futures([a, b, c], prices, [d1, d2, d3], calendar,
                                               0)] == [D("92.4"), D("115.5"), D(110)]
    assert [b.close for b in continuous_futures([a, b, c], prices, [d1, d2, d3], calendar,
                                               0, Adjustment.DIFFERENCE)] == [D(95), D(115), D(110)]
    del prices[b.instrument_id][d3]
    with pytest.raises(RollError, match="Missing"):
        continuous_futures([a, b, c], prices, [d1, d2, d3], calendar, 0)


@pytest.mark.parametrize("days", [[], [date(2026, 9, 11)], [date(2026, 9, 28)],
                                  [date(2026, 9, 9), date(2026, 9, 9)]])
def test_invalid_history(calendar: VenueCalendar, days: list[date]) -> None:
    with pytest.raises(RollError):
        continuous_futures([contract(0)], {}, days, calendar)


def test_source_constraints() -> None:
    source = Path(roll.__file__).read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 280
    for forbidden in ("holidays", "weekday", "asyncpg", "httpx", "datetime.now", "# type: ignore"):
        assert forbidden not in source
    assert "calendar.sessions_for(" in source
    assert "calendar.trading_day_of(" in source



@pytest.mark.parametrize("changes", [
    {"underlying_id": "01ARZ3NDEKTSV4RRFFQ69G5FAZ"},
    {"contract_multiplier": D(2)}, {"quote": "EUR"}, {"currency": "EUR"},
    {"contract_multiplier": D("NaN")},
])
def test_incompatible_chain(calendar: VenueCalendar, changes: dict[str, object]) -> None:
    with pytest.raises(RollError):
        continuous_futures([contract(0), contract(1, 25).model_copy(update=changes)],
                           {}, [date(2026, 9, 9)], calendar)


def test_local_expiry_date_and_continuous_calendar() -> None:
    tz = ZoneInfo("Asia/Seoul")
    cal = VenueCalendar("TEST", tz, SessionSpec(tz, time.min, time.min, frozenset(), True))
    expiry = datetime(2026, 9, 11, 16, tzinfo=timezone.utc)
    assert roll_date(expiry, cal, 0) == date(2026, 9, 12)
    assert roll_date(expiry, cal, 1) == date(2026, 9, 11)


@pytest.mark.parametrize("method,expected", [
    (Adjustment.RATIO, D("92.4")), (Adjustment.DIFFERENCE, D(95)),
])
def test_sparse_history_crosses_two_rolls(
    calendar: VenueCalendar, method: Adjustment, expected: Decimal,
) -> None:
    a, b, c = contract(0, 10), contract(1, 18), contract(2, 25)
    start, end = date(2026, 9, 9), date(2026, 9, 21)
    prices = {a.instrument_id: {start: D(80), date(2026, 9, 10): D(100)},
              b.instrument_id: {date(2026, 9, 10): D(105), date(2026, 9, 18): D(100)},
              c.instrument_id: {date(2026, 9, 18): D(110), end: D(120)}}
    result = continuous_futures([a, b, c], prices, [start, end], calendar, 0, method)
    assert [bar.close for bar in result] == [expected, D(120)]
    assert [bar.instrument_id for bar in result] == [a.instrument_id, c.instrument_id]


def test_history_start_after_roll_needs_no_past_anchors(calendar: VenueCalendar) -> None:
    a, b = contract(0), contract(1, 25)
    day = date(2026, 9, 14)
    result = continuous_futures([a, b], {b.instrument_id: {day: D(110)}}, [day], calendar)
    assert result[0].close == D(110)
    assert result[0].instrument_id == b.instrument_id


def test_difference_supports_negative_prices(calendar: VenueCalendar) -> None:
    a, b = contract(0), contract(1, 25)
    before, boundary = date(2026, 9, 9), date(2026, 9, 10)
    prices = {a.instrument_id: {before: D(-40), boundary: D(-30)},
              b.instrument_id: {boundary: D(10)}}
    result = continuous_futures([a, b], prices, [before, boundary], calendar,
                                adjustment=Adjustment.DIFFERENCE)
    assert [bar.close for bar in result] == [D(0), D(10)]
