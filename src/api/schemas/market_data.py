"""LA-24 — market_data HTTP 읽기 API 응답 스키마.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

캔들 응답은 `contracts/v1.CandleSeries`/`ReplaySeries`를 **상속**해 계약
필드(key/candles/gaps/adjustment/as_of/series_hash/schema_version)를 한 글자도
바꾸지 않고, 스펙 문장 "둘 다 허용, 응답에 둘 다 표기"에 따라 `instrument_id`·
`symbol`·`canonical_symbol`과 이용권 판정 결과(`entitlement`)만 얹는다(107번
§8 "필드 추가는 minor"). 프론트 `parseCandleSeries`(shared-types)는 계약
필드만 읽으므로 부가 필드에 영향받지 않는다.

인스트루먼트 항목은 `InstrumentRef`, 별칭 항목은 `ports/reference_repository.
SymbolAliasRef`를 그대로 쓴다(새 DTO 금지 — 프론트 `parseInstrumentView`/
`parseSymbolAlias`가 기대하는 필드 집합과 동일).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.foundation.market_data.contracts.v1 import CandleSeries, InstrumentRef, ReplaySeries
from src.foundation.market_data.ports.reference_repository import SymbolAliasRef

__all__ = [
    "CandleSeriesView",
    "EntitlementView",
    "InstrumentListView",
    "ReplaySeriesView",
    "SymbolAliasRef",
]


class EntitlementView(BaseModel):
    """`Entitlement`(DC-9)의 허용 결과만 노출한다 — 거부는 응답이 아니라
    404로 끝나므로(타 테넌트 동형) 여기 도달하지 않는다."""

    mode: Literal["realtime", "delayed"]
    delayed_seconds: int


class _SeriesIdentity(BaseModel):
    instrument_id: UUID
    symbol: str
    canonical_symbol: str
    entitlement: EntitlementView


class CandleSeriesView(CandleSeries, _SeriesIdentity):
    pass


class ReplaySeriesView(ReplaySeries, _SeriesIdentity):
    pass


class InstrumentListView(BaseModel):
    """프론트 `toInstrumentListResult`(clients/marketData.ts)가 `items`/
    `next_cursor`를 data 안에서 읽는다 — `meta.page.next_cursor`에도 같은
    값을 싣지만 data 쪽 필드가 그 클라이언트의 계약이다."""

    items: list[InstrumentRef]
    next_cursor: str | None
