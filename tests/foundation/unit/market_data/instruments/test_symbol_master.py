"""DC-2 — `domain/instruments/symbol_master.py` 단위 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-2, §3.2, §4.2, §9.2 DC-2("충돌·대소문자·재상장 규칙").
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.instruments import symbol_master as sm

_ID_A = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
_ID_B = "01BXQR6X4TVQFP7NM5H7J1K2C3"


def _t(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


def _instrument(instrument_id: str, state: InstrumentLifecycle, **overrides: object) -> Instrument:
    base: dict[str, object] = dict(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=state,
        created_at=_t(1),
    )
    base.update(overrides)
    return Instrument(**base)  # type: ignore[arg-type]


def _listing(
    instrument_id: str, symbol: str, listed: int, delisted: int | None = None
) -> VenueListing:
    return VenueListing(
        instrument_id=instrument_id,
        venue=Venue.BITGET,
        venue_symbol=symbol,
        listed_at=_t(listed),
        delisted_at=_t(delisted) if delisted is not None else None,
        is_primary=True,
    )


def _register(**overrides: object) -> sm.InstrumentRef:
    base: dict[str, object] = dict(
        instrument_id=_ID_A,
        venue=Venue.BITGET,
        venue_symbol="BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        listed_at=_t(1),
        created_at=_t(1),
    )
    base.update(overrides)
    return sm.register(**base)  # type: ignore[arg-type]


# ---- resolve ----------------------------------------------------------


def test_resolve_finds_active_listing() -> None:
    instrument = _instrument(_ID_A, InstrumentLifecycle.ACTIVE)
    listing = _listing(_ID_A, "BTCUSDT", 1)
    ref = sm.resolve(Venue.BITGET, "BTCUSDT", instruments=[instrument], listings=[listing])
    assert ref.instrument.instrument_id == _ID_A
    assert ref.listing.venue_symbol == "BTCUSDT"


def test_resolve_case_insensitive_match() -> None:
    instrument = _instrument(_ID_A, InstrumentLifecycle.ACTIVE)
    listing = _listing(_ID_A, "BTCUSDT", 1)
    ref = sm.resolve(Venue.BITGET, "btcusdt", instruments=[instrument], listings=[listing])
    assert ref.instrument.instrument_id == _ID_A


def test_resolve_ignores_delisted_listing_without_as_of() -> None:
    instrument = _instrument(_ID_A, InstrumentLifecycle.DELISTED)
    listing = _listing(_ID_A, "BTCUSDT", 1, delisted=5)
    with pytest.raises(sm.InstrumentNotFoundError):
        sm.resolve(Venue.BITGET, "BTCUSDT", instruments=[instrument], listings=[listing])


def test_resolve_as_of_finds_historical_listing() -> None:
    instrument = _instrument(_ID_A, InstrumentLifecycle.DELISTED)
    listing = _listing(_ID_A, "BTCUSDT", 1, delisted=5)
    ref = sm.resolve(
        Venue.BITGET, "BTCUSDT", instruments=[instrument], listings=[listing], as_of=_t(3)
    )
    assert ref.listing.venue_symbol == "BTCUSDT"


def test_resolve_missing_raises_not_found() -> None:
    with pytest.raises(sm.InstrumentNotFoundError):
        sm.resolve(Venue.BITGET, "BTCUSDT", instruments=[], listings=[])


def test_resolve_naive_as_of_rejected() -> None:
    with pytest.raises(sm.SymbolMasterError):
        sm.resolve(
            Venue.BITGET, "BTCUSDT", instruments=[], listings=[], as_of=datetime(2026, 1, 1)
        )


def test_resolve_invalid_symbol_format_rejected() -> None:
    with pytest.raises(sm.SymbolMasterError):
        sm.resolve(Venue.KIS_KRX, "NOTACODE", instruments=[], listings=[])


# ---- register -----------------------------------------------------------


def test_register_creates_pending_instrument_and_listing() -> None:
    ref = _register()
    assert ref.instrument.instrument_id == _ID_A
    assert ref.instrument.lifecycle_state is InstrumentLifecycle.PENDING
    assert ref.listing.venue_symbol == "BTCUSDT"
    assert ref.listing.delisted_at is None


def test_register_normalizes_lowercase_symbol() -> None:
    ref = _register(venue_symbol="btcusdt")
    assert ref.listing.venue_symbol == "BTCUSDT"


def test_register_invalid_symbol_format_rejected() -> None:
    with pytest.raises(sm.SymbolMasterError):
        _register(venue=Venue.KIS_KRX, venue_symbol="NOTACODE")


def test_register_naive_listed_at_rejected() -> None:
    with pytest.raises(sm.SymbolMasterError):
        _register(listed_at=datetime(2026, 1, 1))


def test_register_duplicate_active_instrument_id_rejected() -> None:
    existing = _instrument(_ID_A, InstrumentLifecycle.ACTIVE)
    with pytest.raises(sm.SymbolConflictError):
        _register(existing_instruments=[existing])


def test_register_relisting_reuse_of_delisted_id_rejected() -> None:
    existing = _instrument(_ID_A, InstrumentLifecycle.DELISTED)
    with pytest.raises(sm.RelistingReuseError):
        _register(existing_instruments=[existing])


def test_register_overlapping_venue_symbol_rejected() -> None:
    existing_listing = _listing(_ID_B, "BTCUSDT", 1)  # 아직 open(delisted_at None)
    with pytest.raises(sm.SymbolConflictError):
        _register(existing_listings=[existing_listing])


def test_register_after_prior_listing_closed_is_allowed() -> None:
    existing_listing = _listing(_ID_B, "BTCUSDT", 1, delisted=1)  # 정확히 listed_at 이전에 닫힘
    ref = _register(existing_listings=[existing_listing])
    assert ref.listing.instrument_id == _ID_A


# ---- change_symbol --------------------------------------------------------


def test_change_symbol_closes_old_and_opens_new_with_same_instrument_id() -> None:
    current = _listing(_ID_A, "BTCUSDT", 1)
    closed, new = sm.change_symbol(current=current, new_venue_symbol="XBTUSDT", changed_at=_t(5))
    assert closed.delisted_at == _t(5)
    assert closed.venue_symbol == "BTCUSDT"
    assert new.instrument_id == _ID_A
    assert new.venue_symbol == "XBTUSDT"
    assert new.delisted_at is None
    assert new.listed_at == _t(5)


def test_change_symbol_normalizes_lowercase() -> None:
    current = _listing(_ID_A, "BTCUSDT", 1)
    _closed, new = sm.change_symbol(current=current, new_venue_symbol="xbtusdt", changed_at=_t(5))
    assert new.venue_symbol == "XBTUSDT"


def test_change_symbol_already_delisted_rejected() -> None:
    current = _listing(_ID_A, "BTCUSDT", 1, delisted=3)
    with pytest.raises(sm.SymbolMasterError):
        sm.change_symbol(current=current, new_venue_symbol="XBTUSDT", changed_at=_t(5))


def test_change_symbol_before_listed_at_rejected() -> None:
    current = _listing(_ID_A, "BTCUSDT", 5)
    with pytest.raises(sm.SymbolMasterError):
        sm.change_symbol(current=current, new_venue_symbol="XBTUSDT", changed_at=_t(1))


def test_change_symbol_overlapping_target_symbol_rejected() -> None:
    current = _listing(_ID_A, "BTCUSDT", 1)
    other = _listing(_ID_B, "XBTUSDT", 1)  # 아직 open
    with pytest.raises(sm.SymbolConflictError):
        sm.change_symbol(
            current=current,
            new_venue_symbol="XBTUSDT",
            changed_at=_t(5),
            existing_listings=[current, other],
        )


def test_change_symbol_naive_changed_at_rejected() -> None:
    current = _listing(_ID_A, "BTCUSDT", 1)
    with pytest.raises(sm.SymbolMasterError):
        sm.change_symbol(
            current=current, new_venue_symbol="XBTUSDT", changed_at=datetime(2026, 1, 5)
        )
