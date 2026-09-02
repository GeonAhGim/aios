"""정규 심볼 ↔ 거래소 심볼 단일 원천(L4 명세 §2-A, R8).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-A, §9 L4-04.

R8 — "주문 시 BTC/USDT, 조회 시 BTCUSDT"를 각 믹스인이 손으로
`replace("/", "")`하던 결함의 근본 수정. 미등록 심볼은 fail-closed
(`UnknownSymbolError`) — 등록되지 않은 심볼로는 주문 자체가 불가능하다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.services.oms.domain.errors import UnknownSymbolError


@dataclass(frozen=True)
class SymbolSpec:
    canonical: str
    venue: str
    venue_symbol: str
    tick: Decimal
    lot: Decimal
    min_notional: Decimal
    quote_ccy: str


class SymbolRegistry:
    def __init__(self) -> None:
        self._by_canonical: dict[tuple[str, str], SymbolSpec] = {}
        self._by_venue_symbol: dict[tuple[str, str], SymbolSpec] = {}

    def register(
        self,
        canonical: str,
        venue: str,
        venue_symbol: str,
        *,
        tick: Decimal,
        lot: Decimal,
        min_notional: Decimal,
        quote_ccy: str,
    ) -> None:
        spec = SymbolSpec(
            canonical=canonical,
            venue=venue,
            venue_symbol=venue_symbol,
            tick=tick,
            lot=lot,
            min_notional=min_notional,
            quote_ccy=quote_ccy,
        )
        self._by_canonical[(canonical, venue)] = spec
        self._by_venue_symbol[(venue_symbol, venue)] = spec

    def spec(self, canonical: str, venue: str) -> SymbolSpec:
        found = self._by_canonical.get((canonical, venue))
        if found is None:
            raise UnknownSymbolError(canonical, venue)
        return found

    def to_venue(self, canonical: str, venue: str) -> str:
        return self.spec(canonical, venue).venue_symbol

    def to_canonical(self, venue_symbol: str, venue: str) -> str:
        found = self._by_venue_symbol.get((venue_symbol, venue))
        if found is None:
            raise UnknownSymbolError(venue_symbol, venue)
        return found.canonical
