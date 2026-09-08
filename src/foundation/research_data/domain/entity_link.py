"""RD-5 -- domain/entity_link.py: deterministic-key entity <-> instrument_id mapping.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 2.1
domain/entity_link.py, Sec 4 RD-A4, Sec 9 RD-5.

Only four key shapes are ever mapped: a KRX 6-digit code, a 13-digit
corporate registration number, a checksum-valid ISIN, or a "TICKER:VENUE"
pair. Anything else (a bare company name, a malformed code) yields
`UnmappedReason.NO_DETERMINISTIC_KEY` -- RD-A4 forbids name-similarity
guessing, so there is no fallback path that "tries harder".

KRX-code and ticker+venue shapes are validated through LA-7's
`to_canonical` (`domain/reference/symbol_normalizer.py`) instead of a
private regex here -- this module does not reimplement venue symbol
rules. The actual instrument_id lookup for any key shape is delegated to
the injected `EntityResolver` (a real implementation would compose
`symbol_master.resolve()` for venue/symbol shapes); this module never
touches instrument/listing master data itself (I/O-free, L0-2).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
)
from src.foundation.research_data.contracts.v1 import ResearchItem

__all__ = [
    "EntityKeyKind",
    "EntityKey",
    "UnmappedReason",
    "EntityLinkResult",
    "EntityResolver",
    "is_valid_isin",
    "extract_entity_key",
    "link_item",
]

_CORP_REG_NO_LEN = 13
_ISIN_LEN = 12


class EntityKeyKind(enum.Enum):
    KRX_CODE = "krx_code"
    CORP_REG_NO = "corp_reg_no"
    ISIN = "isin"
    TICKER_VENUE = "ticker_venue"


@dataclass(frozen=True)
class EntityKey:
    """One deterministic identifier extracted from a raw `ResearchItem.instruments` entry."""

    kind: EntityKeyKind
    value: str
    venue: Venue | None = None


class UnmappedReason(str, enum.Enum):
    NO_DETERMINISTIC_KEY = "no_deterministic_key"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class EntityLinkResult:
    item_id: UUID
    instrument_id: str | None
    reason: UnmappedReason | None


@runtime_checkable
class EntityResolver(Protocol):
    def resolve(self, key: EntityKey) -> str | None:
        """Return the matching `instrument_id`, or `None` if this exact key
        has no match (not-found is not an error -- RD-5 leaves it unmapped).
        Must only do exact deterministic lookups, never guess from
        `key.value`."""
        ...


def is_valid_isin(candidate: str) -> bool:
    """ISO 6166 check-digit validation (Luhn over the letter->digit expansion).

    Not delegated to LA-7 -- an ISIN checksum is not a venue/symbol rule and
    no existing module in this repo implements it.
    """
    if (
        len(candidate) != _ISIN_LEN
        or not candidate[:2].isalpha()
        or not candidate[2:].isalnum()
    ):
        return False
    body, check_digit = candidate[:11], candidate[11]
    if not check_digit.isdigit():
        return False
    numeric = "".join(str(int(ch, 36)) for ch in body)
    digits = numeric + check_digit
    total = 0
    for position, ch in enumerate(reversed(digits)):
        digit = int(ch)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _krx_code_key(raw: str) -> EntityKey | None:
    try:
        canonical = to_canonical(Venue.KIS_KRX, raw)
    except SymbolNormalizationError:
        return None
    return EntityKey(EntityKeyKind.KRX_CODE, canonical)


def _corp_reg_no_key(raw: str) -> EntityKey | None:
    if len(raw) == _CORP_REG_NO_LEN and raw.isdigit():
        return EntityKey(EntityKeyKind.CORP_REG_NO, raw)
    return None


def _isin_key(raw: str) -> EntityKey | None:
    if is_valid_isin(raw):
        return EntityKey(EntityKeyKind.ISIN, raw)
    return None


def _ticker_venue_key(raw: str) -> EntityKey | None:
    ticker, sep, venue_name = raw.partition(":")
    if not sep:
        return None
    try:
        venue = Venue[venue_name.strip().upper()]
    except KeyError:
        return None
    try:
        canonical = to_canonical(venue, ticker.strip().upper())
    except SymbolNormalizationError:
        return None
    return EntityKey(EntityKeyKind.TICKER_VENUE, canonical, venue=venue)


_KEY_PARSERS = (_krx_code_key, _corp_reg_no_key, _isin_key, _ticker_venue_key)


def extract_entity_key(raw: str) -> EntityKey | None:
    """Classify one raw `instruments` entry into a deterministic key, or
    `None` if it matches none of the four RD-A4 shapes (e.g. a company
    name)."""
    candidate = raw.strip()
    if not candidate:
        return None
    for parser in _KEY_PARSERS:
        key = parser(candidate)
        if key is not None:
            return key
    return None


def link_item(item: ResearchItem, resolver: EntityResolver) -> EntityLinkResult:
    """Map one `ResearchItem` to an `instrument_id`, or leave it unmapped.

    Tries each raw `item.instruments` entry in order: the first one that
    both parses into a deterministic key and resolves via `resolver` wins.
    Never raises for an unmapped item -- unmapped is a normal, expected
    outcome (RD-5 DoD (c)), not an error.
    """
    keys = [
        key for key in (extract_entity_key(raw) for raw in item.instruments) if key is not None
    ]
    if not keys:
        return EntityLinkResult(item.item_id, None, UnmappedReason.NO_DETERMINISTIC_KEY)
    for key in keys:
        instrument_id = resolver.resolve(key)
        if instrument_id is not None:
            return EntityLinkResult(item.item_id, instrument_id, None)
    return EntityLinkResult(item.item_id, None, UnmappedReason.NOT_FOUND)
