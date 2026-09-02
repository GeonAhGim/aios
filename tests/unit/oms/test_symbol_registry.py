"""정규 심볼 ↔ 거래소 심볼 레지스트리 단위테스트 — L4-04. DB 없음."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.oms.domain.errors import UnknownSymbolError
from src.services.oms.domain.symbol_registry import SymbolRegistry


@pytest.fixture
def registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT",
        "bitget",
        "BTCUSDT",
        tick=Decimal("0.01"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


def test_to_venue_converts_canonical_to_venue_symbol(registry: SymbolRegistry) -> None:
    assert registry.to_venue("BTC/USDT", "bitget") == "BTCUSDT"


def test_to_canonical_converts_venue_symbol_back(registry: SymbolRegistry) -> None:
    assert registry.to_canonical("BTCUSDT", "bitget") == "BTC/USDT"


def test_unregistered_canonical_symbol_fails_closed(registry: SymbolRegistry) -> None:
    with pytest.raises(UnknownSymbolError):
        registry.to_venue("ETH/USDT", "bitget")


def test_unregistered_venue_symbol_fails_closed(registry: SymbolRegistry) -> None:
    with pytest.raises(UnknownSymbolError):
        registry.to_canonical("ETHUSDT", "bitget")


def test_same_canonical_symbol_different_venue_is_unregistered(registry: SymbolRegistry) -> None:
    """R8 — 심볼 등록은 venue별로 독립이다(같은 canonical이라도 다른
    venue엔 등록 안 됐을 수 있음)."""
    with pytest.raises(UnknownSymbolError):
        registry.to_venue("BTC/USDT", "kis")


def test_spec_carries_tick_lot_min_notional(registry: SymbolRegistry) -> None:
    spec = registry.spec("BTC/USDT", "bitget")
    assert spec.tick == Decimal("0.01")
    assert spec.lot == Decimal("0.0001")
    assert spec.min_notional == Decimal("5")
    assert spec.quote_ccy == "USDT"
