"""LA-7 — single rule for venue raw symbol ↔ canonical symbol conversion (pure functions).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-7, §9.2 LA-7.

Crypto (BITGET) uses "BASE/QUOTE" slash notation, KRX uses 6-digit stock codes,
US stocks use the raw ticker as-is as the canonical form (§2.2 "US ticker single rule").
The goal is to converge conversion logic scattered across adapters such as
`src/exchanges/bitget/symbols.py` into this file, but adapter wiring changes
themselves belong to LA-19, so existing adapters (`src/exchanges/**`) are not touched. No I/O.

BR-21(task-7868) — OKX uses "BASE-QUOTE" dash notation (its `instId`) for the
same canonical "BASE/QUOTE" representation as BITGET; only the raw-symbol
separator differs, so `_okx_raw_to_canonical`/`_okx_canonical_to_raw` reuse
the same `_CRYPTO_QUOTES` table as the BITGET branch (symmetric precedent).
A raw/canonical string using the wrong venue's separator (e.g. "BTC-USDT"
where a canonical "BTC/USDT" is expected) is rejected, not silently
normalized -- fail-closed per the module's existing contract.
"""

from __future__ import annotations

import re

from src.foundation.market_data.contracts.v1 import Venue

__all__ = ["SymbolNormalizationError", "to_canonical", "to_venue"]

# Unvalidated: common quotes, not cross-checked vs exchange docs
# (priority: longest suffix first).
_CRYPTO_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "BTC", "ETH", "KRW")
_KRX_CODE = re.compile(r"\d{6}")
_US_TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z])?")


class SymbolNormalizationError(ValueError):
    """`MD_SYMBOL_UNKNOWN` — cannot interpret venue raw or canonical symbol."""


def to_canonical(venue: Venue, raw: str) -> str:
    """Convert venue raw symbol to canonical representation."""
    if venue is Venue.BITGET:
        return _crypto_raw_to_canonical(raw)
    if venue is Venue.OKX:
        return _okx_raw_to_canonical(raw)
    if venue is Venue.KIS_KRX:
        return _krx_validate(raw)
    if venue is Venue.NH_KRX:
        return _krx_validate(raw)
    if venue is Venue.KIS_US:
        return _us_validate(raw)
    raise SymbolNormalizationError(f"알 수 없는 venue: {venue!r}")


def to_venue(venue: Venue, canonical: str) -> str:
    """Convert canonical representation to venue raw symbol."""
    if venue is Venue.BITGET:
        return _crypto_canonical_to_raw(canonical)
    if venue is Venue.OKX:
        return _okx_canonical_to_raw(canonical)
    if venue is Venue.KIS_KRX:
        return _krx_validate(canonical)
    if venue is Venue.NH_KRX:
        return _krx_validate(canonical)
    if venue is Venue.KIS_US:
        return _us_validate(canonical)
    raise SymbolNormalizationError(f"알 수 없는 venue: {venue!r}")


def _crypto_raw_to_canonical(raw: str) -> str:
    for quote in sorted(_CRYPTO_QUOTES, key=len, reverse=True):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[: -len(quote)]}/{quote}"
    raise SymbolNormalizationError(f"미지 quote: {raw!r}")


def _crypto_canonical_to_raw(canonical: str) -> str:
    base, sep, quote = canonical.partition("/")
    if not sep or not base or quote not in _CRYPTO_QUOTES:
        raise SymbolNormalizationError(f"미지 quote: {canonical!r}")
    return f"{base}{quote}"


def _okx_raw_to_canonical(raw: str) -> str:
    base, sep, quote = raw.partition("-")
    if not sep or not base or quote not in _CRYPTO_QUOTES:
        raise SymbolNormalizationError(f"미지 OKX instId: {raw!r}")
    return f"{base}/{quote}"


def _okx_canonical_to_raw(canonical: str) -> str:
    base, sep, quote = canonical.partition("/")
    if not sep or not base or quote not in _CRYPTO_QUOTES:
        raise SymbolNormalizationError(f"미지 quote: {canonical!r}")
    return f"{base}-{quote}"


def _krx_validate(symbol: str) -> str:
    if not _KRX_CODE.fullmatch(symbol):
        raise SymbolNormalizationError(f"KRX 6자리 코드 아님: {symbol!r}")
    return symbol


def _us_validate(symbol: str) -> str:
    if not _US_TICKER.fullmatch(symbol):
        raise SymbolNormalizationError(f"US 티커 형식 아님: {symbol!r}")
    return symbol
