"""LA-1 — Enum definitions for market_data contract v1 (pure value sets).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

`contracts/v1.py` re-exports these enums as-is (`from .v1_enums import
*`) — only the value definitions were split out here because of the
300-line cap (architecture P6); the principle that `contracts.v1` is the
sole public surface (see the v1.py module docstring) still holds. Callers
keep importing via `from src.foundation.market_data.contracts.v1 import
Venue` etc. and never need to reference this file directly.
"""
from __future__ import annotations

from enum import Enum

__all__ = [
    "Timeframe",
    "Venue",
    "Adjustment",
    "SymbolStatus",
    "QualityIssueType",
    "Severity",
    "Verdict",
]


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    L2 = "L2"  # RD-19 — marks coverage for raw L2 order book streams, which have no OHLC concept.


class Venue(str, Enum):
    """Lookup key for session rules (A3).

    KIS has different calendars per market, so KRX/US are separated.
    """

    BITGET = "BITGET"
    KIS_KRX = "KIS_KRX"
    KIS_US = "KIS_US"
    BINANCE = "BINANCE"  # RD-19 in-house L2 collector
    BYBIT = "BYBIT"  # RD-19
    OKX = "OKX"  # RD-19
    UPBIT = "UPBIT"  # RD-19


class Adjustment(str, Enum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"


class SymbolStatus(str, Enum):
    PENDING = "PENDING"
    LISTED = "LISTED"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"


class QualityIssueType(str, Enum):
    OHLC_INCONSISTENT = "OHLC_INCONSISTENT"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    TIME_MISALIGNED = "TIME_MISALIGNED"
    NAIVE_DATETIME = "NAIVE_DATETIME"
    GAP = "GAP"
    STALE = "STALE"
    SPIKE = "SPIKE"
    DUPLICATE_IDENTICAL = "DUPLICATE_IDENTICAL"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    OUT_OF_SESSION = "OUT_OF_SESSION"


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    REJECT = "REJECT"


class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    PARTIAL = "PARTIAL"
    QUARANTINE = "QUARANTINE"
    REJECT = "REJECT"
