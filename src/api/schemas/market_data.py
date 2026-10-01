"""LA-24 — market_data HTTP read API response schemas.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

The candle response **inherits** `contracts/v1.CandleSeries`/`ReplaySeries`
without changing a single character of the contract fields
(key/candles/gaps/adjustment/as_of/series_hash/schema_version), and per the
spec's "allow both, surface both in the response" wording it only adds
`instrument_id`/`symbol`/`canonical_symbol` and the entitlement decision
(`entitlement`) (standard-107 §8 "adding a field is minor"). The frontend's
`parseCandleSeries` (shared-types) only reads the contract fields, so it is
unaffected by the extra fields.

Instrument items reuse `InstrumentRef` as-is, and alias items reuse
`ports/reference_repository.SymbolAliasRef` as-is (no new DTO — identical to
the field set expected by the frontend's `parseInstrumentView`/
`parseSymbolAlias`).
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
    """Exposes only the allowed outcome of `Entitlement` (DC-9) — a denial
    never reaches here, since it ends as a 404 (isomorphic to another tenant)
    instead of a response."""

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
    """The frontend's `toInstrumentListResult` (clients/marketData.ts) reads
    `items`/`next_cursor` from inside `data` — the same value is also carried
    on `meta.page.next_cursor`, but the `data`-side field is that client's
    contract."""

    items: list[InstrumentRef]
    next_cursor: str | None
