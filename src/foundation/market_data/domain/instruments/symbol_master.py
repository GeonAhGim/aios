"""DC-2 — instrument_id issuance, venue symbol mapping, conflict rules (pure).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-2, §3.2 (symbol master), §4.2 (lifecycle transition table), §9.2 DC-2.

`resolve()`: (venue, symbol) -> `InstrumentRef` (§3.2 `Instrument` + matched
`VenueListing`). `register()`: new instrument + first listing. `change_symbol()`:
§3.2 symbol change (old listing `delisted_at` set + new listing, `instrument_id`
immutable). §4.2 delisted→relisted "create new instrument (old id reuse forbidden)" guard
is enforced by `register()` — reusing an old delisted id raises `RelistingReuseError`.

Pure (no I/O · no asyncpg imports, L0-2). `instrument_id` is issued by the caller
beforehand — if this function generated ULIDs directly, determinism breaks.

Reuses existing LA-7 `domain/reference/symbol_normalizer.py` (not a re-implementation) —
this file adds case-normalization, registration conflict, and relisting rules on top.

Unverified: interval overlap check ([listed_at, delisted_at) half-open) is a pre-check
reproducing the §4.1 DB EXCLUDE (gist) constraint at the pure layer — actual enforcement
is DC-4's responsibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
)

__all__ = [
    "InstrumentNotFoundError",
    "InstrumentRef",
    "RelistingReuseError",
    "SymbolConflictError",
    "SymbolMasterError",
    "change_symbol",
    "register",
    "resolve",
]


@dataclass(frozen=True)
class InstrumentRef:
    """Composite view of `Instrument` + matched `VenueListing`
    (`resolve()` only, non-persistent)."""

    instrument: Instrument
    listing: VenueListing


class SymbolMasterError(ValueError):
    """DC-2 common failure (fail-closed) — never silently returns None/default values."""


class InstrumentNotFoundError(SymbolMasterError):
    """`resolve()` found no active `VenueListing` matching (venue, symbol)."""


class SymbolConflictError(SymbolMasterError):
    """§4.1 new registration/symbol change violates "no overlapping venue_listings periods"."""


class RelistingReuseError(SymbolMasterError):
    """§4.2 delisted→relisted "create new instrument (old id forbidden)" violated."""


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise SymbolMasterError(f"{label} accepts tz-aware datetime only")


def _normalize_symbol(venue: Venue, raw_symbol: str) -> str:
    """Case-normalize (uppercase) then LA-7 format validation (reuse, not re-implementation).

    Non-str input (e.g. a deserialization path passing None/int) must not
    leak AttributeError from .strip() -- fail-closed as SymbolMasterError.
    """
    if not isinstance(raw_symbol, str):
        raise SymbolMasterError(f"venue_symbol must be str: {raw_symbol!r}")
    candidate = raw_symbol.strip().upper()
    try:
        to_canonical(venue, candidate)
    except SymbolNormalizationError as exc:
        raise SymbolMasterError(f"venue_symbol format error: {raw_symbol!r}") from exc
    return candidate


def _is_active_at(listing: VenueListing, as_of: datetime) -> bool:
    if listing.listed_at > as_of:
        return False
    return listing.delisted_at is None or listing.delisted_at > as_of


def _overlaps(
    a_start: datetime, a_end: datetime | None, b_start: datetime, b_end: datetime | None
) -> bool:
    """Overlap of half-open intervals `[a_start, a_end)` vs
    `[b_start, b_end)` (`None` = +infinity)."""
    if a_end is not None and a_end <= b_start:
        return False
    if b_end is not None and b_end <= a_start:
        return False
    return True


def _check_no_overlap(
    listings: Sequence[VenueListing], venue: Venue, venue_symbol: str, opens_at: datetime
) -> None:
    """Reject if a new interval opening at `opens_at` overlaps with an existing
    (venue, venue_symbol) interval in `listings`."""
    for listing in listings:
        if listing.venue is not venue or listing.venue_symbol != venue_symbol:
            continue
        if _overlaps(listing.listed_at, listing.delisted_at, opens_at, None):
            raise SymbolConflictError(
                f"overlapping venue_symbol interval: venue={venue.value} symbol={venue_symbol!r}"
            )


def _find_instrument(instruments: Sequence[Instrument], instrument_id: str) -> Instrument:
    for instrument in instruments:
        if instrument.instrument_id == instrument_id:
            return instrument
    raise InstrumentNotFoundError(f"listing exists but no instrument record: {instrument_id!r}")


def resolve(
    venue: Venue,
    symbol: str,
    *,
    instruments: Sequence[Instrument],
    listings: Sequence[VenueListing],
    as_of: datetime | None = None,
) -> InstrumentRef:
    """`(venue, symbol)` -> active `InstrumentRef` (case-normalized match). Without
    `as_of`, only current active; match failure raises `InstrumentNotFoundError` (not `None`)."""
    if as_of is not None:
        _require_aware(as_of, "as_of")
    normalized = _normalize_symbol(venue, symbol)
    for listing in listings:
        if listing.venue is not venue or listing.venue_symbol != normalized:
            continue
        if as_of is None:
            if listing.delisted_at is not None:
                continue
        elif not _is_active_at(listing, as_of):
            continue
        instrument = _find_instrument(instruments, listing.instrument_id)
        return InstrumentRef(instrument=instrument, listing=listing)
    raise InstrumentNotFoundError(f"no matching instrument: venue={venue.value} symbol={symbol!r}")


def register(
    *,
    instrument_id: str,
    venue: Venue,
    venue_symbol: str,
    asset_class: AssetClass,
    tick_size: Decimal,
    lot_size: Decimal,
    calendar_id: str,
    listed_at: datetime,
    created_at: datetime,
    base: str | None = None,
    quote: str | None = None,
    isin: str | None = None,
    figi: str | None = None,
    is_primary: bool = True,
    existing_instruments: Sequence[Instrument] = (),
    existing_listings: Sequence[VenueListing] = (),
) -> InstrumentRef:
    """New `Instrument` (state `PENDING`) + first `VenueListing`. Reject (fail-closed):
    duplicate non-DELISTED id, reuse of existing DELISTED id (`RelistingReuseError`,
    relisting rule), `(venue, venue_symbol)` interval overlap."""
    _require_aware(listed_at, "listed_at")
    _require_aware(created_at, "created_at")
    normalized = _normalize_symbol(venue, venue_symbol)
    for instrument in existing_instruments:
        if instrument.instrument_id != instrument_id:
            continue
        if instrument.lifecycle_state is InstrumentLifecycle.DELISTED:
            raise RelistingReuseError(
                f"delisted instrument_id reuse forbidden "
                f"(relisting requires new id): {instrument_id!r}"
            )
        raise SymbolConflictError(f"already registered instrument_id: {instrument_id!r}")
    _check_no_overlap(existing_listings, venue, normalized, listed_at)
    instrument = Instrument(
        instrument_id=instrument_id,
        asset_class=asset_class,
        base=base,
        quote=quote,
        isin=isin,
        figi=figi,
        tick_size=tick_size,
        lot_size=lot_size,
        calendar_id=calendar_id,
        lifecycle_state=InstrumentLifecycle.PENDING,
        created_at=created_at,
    )
    listing = VenueListing(
        instrument_id=instrument_id,
        venue=venue,
        venue_symbol=normalized,
        listed_at=listed_at,
        delisted_at=None,
        is_primary=is_primary,
    )
    return InstrumentRef(instrument=instrument, listing=listing)


def change_symbol(
    *,
    current: VenueListing,
    new_venue_symbol: str,
    changed_at: datetime,
    existing_listings: Sequence[VenueListing] = (),
    is_primary: bool | None = None,
) -> tuple[VenueListing, VenueListing]:
    """§3.2 symbol change: new `VenueListing` (old listing `delisted_at` set),
    `instrument_id` immutable. Returns `(closed_old, new)`."""
    _require_aware(changed_at, "changed_at")
    if current.delisted_at is not None:
        raise SymbolMasterError("already delisted listing is not a symbol-change target")
    if changed_at <= current.listed_at:
        raise SymbolMasterError("changed_at must be after listed_at")
    normalized = _normalize_symbol(current.venue, new_venue_symbol)
    key = (current.venue, current.venue_symbol, current.listed_at)
    others = [x for x in existing_listings if (x.venue, x.venue_symbol, x.listed_at) != key]
    _check_no_overlap(others, current.venue, normalized, changed_at)
    closed = current.model_copy(update={"delisted_at": changed_at})
    new_listing = VenueListing(
        instrument_id=current.instrument_id,
        venue=current.venue,
        venue_symbol=normalized,
        listed_at=changed_at,
        delisted_at=None,
        is_primary=current.is_primary if is_primary is None else is_primary,
    )
    return closed, new_listing
