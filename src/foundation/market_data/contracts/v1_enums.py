"""LA-1 — market_data 계약 v1의 enum 정의(순수 값 집합).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

`contracts/v1.py`가 이 enum들을 그대로 재노출한다(`from .v1_enums import
*`) — 300줄 상한(아키텍처 P6)에 걸려 값 정의만 분리했을 뿐, `contracts.v1`
가 유일한 공개 표면이라는 원칙(v1.py 모듈 docstring)은 그대로 유지된다.
호출부는 계속 `from src.foundation.market_data.contracts.v1 import Venue`
등으로 임포트하며 이 파일을 직접 참조할 필요가 없다.
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
    L2 = "L2"  # RD-19 — OHLC 정렬 개념이 없는 원시 L2 호가창 스트림 커버리지 표식.


class Venue(str, Enum):
    """세션 규칙(A3) 조회 키. KIS는 시장별로 캘린더가 달라 KRX/US를 분리한다."""

    BITGET = "BITGET"
    KIS_KRX = "KIS_KRX"
    KIS_US = "KIS_US"
    BINANCE = "BINANCE"  # RD-19 자체 L2 수집기
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
