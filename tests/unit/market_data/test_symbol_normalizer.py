"""LA-7 — symbol_normalizer 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.1 test_symbol_normalizer.py.
"""
from __future__ import annotations

import pytest

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import (
    SymbolNormalizationError,
    to_canonical,
    to_venue,
)


def test_bitget_roundtrip() -> None:
    assert to_canonical(Venue.BITGET, "BTCUSDT") == "BTC/USDT"
    assert to_venue(Venue.BITGET, "BTC/USDT") == "BTCUSDT"


def test_krx_roundtrip() -> None:
    assert to_canonical(Venue.KIS_KRX, "005930") == "005930"
    assert to_venue(Venue.KIS_KRX, "005930") == "005930"


def test_us_roundtrip() -> None:
    assert to_canonical(Venue.KIS_US, "AAPL") == "AAPL"
    assert to_venue(Venue.KIS_US, "AAPL") == "AAPL"


def test_unknown_quote_raw_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.BITGET, "BTCXYZ")


def test_unknown_quote_canonical_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_venue(Venue.BITGET, "BTC/XYZ")


def test_krx_invalid_format_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_KRX, "AAPL")


def test_us_invalid_format_raises() -> None:
    with pytest.raises(SymbolNormalizationError):
        to_canonical(Venue.KIS_US, "aapl")
