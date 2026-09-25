"""LA-9 — Raw candle supply port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application knows only this Protocol; actual implementations
(adapters/bitget_ingest_source.py, adapters/kis_ingest_source.py) do not
(§4 rule 71). Return type is a list of `CandleRecord` that has not yet
passed quality gates ("Raw" denotes pre-validation state, not a new DTO) —
sorting, deduplication, and adjudication are handled by
`domain/quality/*` (LA-4~6) and `application/ingest_candles.py`.
Lookup failures propagate as exceptions — replacing them with an empty
list would be indistinguishable from "no data" (same principle as
positions `ProviderBalanceSource`).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, Timeframe, Venue


@runtime_checkable
class IngestSource(Protocol):
    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
    ) -> list[CandleRecord]:
        """`[start, end)` range. Returns an empty list when no data exists
        (not an error) — supply failures raise an exception."""
        ...
