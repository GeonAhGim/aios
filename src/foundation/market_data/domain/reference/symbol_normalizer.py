"""LA-7 — venue 원시 심볼 ↔ canonical 심볼 변환 단일 규칙(순수 함수).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-7, §9.2 LA-7.

크립토(BITGET)는 "BASE/QUOTE" 슬래시 표기, KRX는 6자리 종목코드, US
주식은 원시 티커를 그대로 canonical로 쓴다(§2.2 "US 티커 단일 규칙").
`src/exchanges/bitget/symbols.py` 등 어댑터별로 흩어진 변환 로직을
이 파일로 수렴하는 것이 목표이나, 어댑터 배선 교체 자체는 LA-19의
몫이라 기존 어댑터(`src/exchanges/**`)는 건드리지 않는다. I/O 없음.
"""
from __future__ import annotations

import re

from src.foundation.market_data.contracts.v1 import Venue

__all__ = ["SymbolNormalizationError", "to_canonical", "to_venue"]

# 미검증: 실거래소 문서 대조 없이 통용 quote만 나열(우선순위: 긴 접미사부터).
_CRYPTO_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "BTC", "ETH", "KRW")
_KRX_CODE = re.compile(r"\d{6}")
_US_TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z])?")


class SymbolNormalizationError(ValueError):
    """`MD_SYMBOL_UNKNOWN` — venue 원시/canonical 심볼을 해석할 수 없음."""


def to_canonical(venue: Venue, raw: str) -> str:
    """venue 원시 심볼 -> canonical 표현."""
    if venue is Venue.BITGET:
        return _crypto_raw_to_canonical(raw)
    if venue is Venue.KIS_KRX:
        return _krx_validate(raw)
    if venue is Venue.KIS_US:
        return _us_validate(raw)
    raise SymbolNormalizationError(f"알 수 없는 venue: {venue!r}")


def to_venue(venue: Venue, canonical: str) -> str:
    """canonical 표현 -> venue 원시 심볼."""
    if venue is Venue.BITGET:
        return _crypto_canonical_to_raw(canonical)
    if venue is Venue.KIS_KRX:
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


def _krx_validate(symbol: str) -> str:
    if not _KRX_CODE.fullmatch(symbol):
        raise SymbolNormalizationError(f"KRX 6자리 코드 아님: {symbol!r}")
    return symbol


def _us_validate(symbol: str) -> str:
    if not _US_TICKER.fullmatch(symbol):
        raise SymbolNormalizationError(f"US 티커 형식 아님: {symbol!r}")
    return symbol
