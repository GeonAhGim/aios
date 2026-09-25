"""DC-1 — Symbol master contract v2.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-1, §3.2 (symbol master), §4.1 (fail-closed invariant), §4.2 (lifecycle
state-transition table), 107_contract_versioning_and_compatibility_standard_v1.0.md
§3.3 (MAJOR change).

`contracts/v1.py`'s `InstrumentRef` was a single record keyed by canonical_symbol
string. This v2 separates `instrument_id` (immutable ULID) from
`VenueListing` (venue-specific symbol mapping, history preserved) — a symbol
change registers a new listing and fills the old listing's `delisted_at`; it
does not overwrite the existing record (§3.2). This is a field-meaning change
(MAJOR), so `v1.py` was left untouched and a new `v2/` module was created
(107 §3.3).

DC-2 (symbol_master), DC-3 (lifecycle), DC-5 (ports), and DC-6 (coverage
registry) depend 1:1 on this contract, so no fields are added arbitrarily
outside the §3.2 table.

All `datetime` fields use `AwareDatetime` to reject naive values, and price-related
numerics (`tick_size`, `lot_size`) use `Decimal` (same precision as NUMERIC(30,10);
float is prohibited).

DC-20 (§9.10) extends `Instrument` with derivative-symbol fields (`kind`,
`underlying_id`, `expiry`, `strike`, `option_right`, `contract_multiplier`,
`settlement`, `currency`, `country`, `mic`). Every new field is optional
with a `None` default, which is a MINOR change under 107 §3.2 ("new
optional field, default or null allowed") — `schema_version` stays
`"instruments-v2"`, no `v3` module. `underlying_id` reuses `ULID` (DC-1's
own type, not a new validator) and points at the spot/base `Instrument`
a derivative is written on; chaining `underlying_id` + `expiry` across a
set of `Instrument` records (e.g. all options on one underlying, grouped
by expiry) is a plain-field query left to callers — this leaf ships the
DTO only, not a query module (roll/continuous-future logic is DC-25,
option-chain API is DC-26). `currency`/`country`/`mic` are format-checked
only (ISO 4217 3-letter, ISO 3166-1 alpha-2, ISO 10383 MIC 4-char) — no
external code table is bundled or validated against.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue

SCHEMA_VERSION: Literal["instruments-v2"] = "instruments-v2"

# Crockford Base32 (uppercase, excluding I/L/O/U), 26 characters; the first
# character is restricted to 0-7 to prevent timestamp overflow (ULID spec).
# This project does not depend on external packages like `python-ulid`; it
# validates only the string format — issuance is the responsibility of
# DC-2 (symbol_master), and this contract enforces format only.
_ULID_PATTERN = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def _validate_ulid(value: str) -> str:
    normalized = value.upper()
    if not _ULID_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid ULID: {value!r}")
    return normalized


ULID = Annotated[str, AfterValidator(_validate_ulid)]

# ISO 4217 currency (3 letters), ISO 3166-1 alpha-2 country, ISO 10383 MIC
# (4 alphanumeric). Format only — no external code table is bundled, so an
# unassigned-but-well-formed code (e.g. "ZZZ") passes; that cross-check is
# out of scope for this contract (see module docstring, DC-20).
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_COUNTRY_PATTERN = re.compile(r"^[A-Z]{2}$")
_MIC_PATTERN = re.compile(r"^[A-Z0-9]{4}$")


def _validate_currency(value: str) -> str:
    normalized = value.upper()
    if not _CURRENCY_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid ISO 4217 currency code: {value!r}")
    return normalized


def _validate_country(value: str) -> str:
    normalized = value.upper()
    if not _COUNTRY_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid ISO 3166-1 alpha-2 country code: {value!r}")
    return normalized


def _validate_mic(value: str) -> str:
    normalized = value.upper()
    if not _MIC_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid ISO 10383 MIC: {value!r}")
    return normalized


CurrencyCode = Annotated[str, AfterValidator(_validate_currency)]
CountryCode = Annotated[str, AfterValidator(_validate_country)]
MIC = Annotated[str, AfterValidator(_validate_mic)]


class InstrumentLifecycle(str, Enum):
    """States from the §4.2 symbol lifecycle transition table. The transition
    rules themselves are the responsibility of DC-3
    (`domain/instruments/lifecycle.py`); this contract defines state values only."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    DELISTED = "DELISTED"


class InstrumentKind(str, Enum):
    """Coarse product taxonomy (DC-20, §9.10). Optional on `Instrument` and
    `None` for records written before this field existed or for callers
    that don't need the distinction — existing spot use is unaffected."""

    SPOT = "SPOT"
    FUTURE = "FUTURE"
    PERP = "PERP"
    OPTION = "OPTION"
    BOND = "BOND"
    FUND = "FUND"
    INDEX = "INDEX"


class OptionRight(str, Enum):
    """`Instrument.option_right`, set only when `kind == OPTION`."""

    CALL = "CALL"
    PUT = "PUT"


class SettlementType(str, Enum):
    """`Instrument.settlement`, relevant for derivatives (`kind` in
    `{FUTURE, PERP, OPTION}`)."""

    CASH = "CASH"
    PHYSICAL = "PHYSICAL"


class Instrument(BaseModel, frozen=True):
    """Symbol master record, venue-independent. `instrument_id` is immutable
    (§4.1) — relisting does not reuse the same id but issues a new
    `Instrument` (§4.2 delisted→relisted: "new instrument creation (old id
    reuse prohibited)").

    `frozen=True` matches every other contract in this package
    (`candle_lineage.TickLineage`, `coverage.CoverageSpan`,
    `microstructure.TradeTick`/`QuoteL1`/`BookL2`) — the §4.1
    `instrument_id` invariant must hold in memory, not just at the DB
    constraint (DC-4). `lifecycle_state` transitions (DC-3) never mutate
    this object in place; the repository reconstructs a fresh
    `Instrument` from the DB `UPDATE ... RETURNING` row, so `frozen=True`
    does not conflict with that flow."""

    instrument_id: ULID
    asset_class: AssetClass
    base: str | None
    quote: str | None
    isin: str | None
    figi: str | None
    tick_size: Decimal
    lot_size: Decimal
    calendar_id: str
    lifecycle_state: InstrumentLifecycle
    created_at: AwareDatetime
    # DC-20 (§9.10): derivative-symbol fields, all optional/None-default
    # (107 §3.2 MINOR) so every existing spot `Instrument` is unaffected.
    kind: InstrumentKind | None = None
    underlying_id: ULID | None = None
    expiry: AwareDatetime | None = None
    strike: Decimal | None = None
    option_right: OptionRight | None = None
    contract_multiplier: Decimal | None = None
    settlement: SettlementType | None = None
    currency: CurrencyCode | None = None
    country: CountryCode | None = None
    mic: MIC | None = None
    schema_version: Literal["instruments-v2"] = SCHEMA_VERSION


class VenueListing(BaseModel, frozen=True):
    """Venue-specific symbol mapping. `(venue, venue_symbol, listed_at)` must
    be unique (§3.2) — this uniqueness is actually enforced by the DC-4
    migration's DB constraint (EXCLUDE), and this DTO expresses only that
    contract shape. A symbol change is expressed by filling `delisted_at` on
    the old listing and registering a new listing (`instrument_id` stays the
    same).

    `frozen=True` for the same reason as `Instrument` (consistency with
    every other contract in this package) — nothing quietly rewrites a
    listing-history record in memory."""

    instrument_id: ULID
    venue: Venue
    venue_symbol: str
    listed_at: AwareDatetime
    delisted_at: AwareDatetime | None
    is_primary: bool
    schema_version: Literal["instruments-v2"] = SCHEMA_VERSION
