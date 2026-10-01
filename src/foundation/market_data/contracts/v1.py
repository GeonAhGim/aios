"""LA-1 — market_data contracts v1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1,
107_contract_versioning_and_compatibility_standard_v1.0.md.

This module is the sole public surface for candle/tick ingestion, quality
verdicts, reference data, and replay. `domain/` imports this file, but this
file never imports `domain/` (same principle as rule 71 §4, FND-03/LB-1/LC-1).
Field additions are minor (rule 107, default required) — removals or meaning
changes go into a new `v2` module instead.

Every `datetime` field is `AwareDatetime`, rejecting naive values, and
prices/quantities are `Decimal` (same precision as NUMERIC(30,10), no float).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1_enums import (
    Adjustment,
    QualityIssueType,
    Severity,
    SymbolStatus,
    Timeframe,
    Venue,
    Verdict,
)

__all__ = [
    "SCHEMA_VERSION",
    "Timeframe",
    "Venue",
    "Adjustment",
    "SymbolStatus",
    "QualityIssueType",
    "Severity",
    "Verdict",
    "SeriesKey",
    "CandleRecord",
    "TickRecord",
    "QualityIssue",
    "QualityVerdict",
    "IngestCandlesCommand",
    "IngestBatchResult",
    "TickIngestBatchResult",
    "CandleQuery",
    "CandleSeries",
    "ReplayRequest",
    "ReplaySeries",
    "SessionWindow",
    "CalendarDay",
    "InstrumentRef",
    "RegisterInstrumentCommand",
    "LifecycleEventCommand",
    "CorporateAction",
    "DataQualityMetrics",
]

SCHEMA_VERSION: Literal["v1"] = "v1"


class SeriesKey(BaseModel):
    """Candle/tick series identifier (venue, instrument, timeframe)."""

    venue: Venue
    instrument_id: UUID
    timeframe: Timeframe
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CandleRecord(BaseModel):
    """Price/quantity stay Decimal at the same precision as the NUMERIC(30,10) storage."""

    key: SeriesKey
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class TickRecord(BaseModel):
    venue: Venue
    instrument_id: UUID
    trade_id: str
    price: Decimal
    quantity: Decimal
    side: Literal["buy", "sell"]
    traded_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class QualityIssue(BaseModel):
    type: QualityIssueType
    severity: Severity
    open_time: AwareDatetime | None
    detail: dict[str, str]
    schema_version: Literal["v1"] = SCHEMA_VERSION


class QualityVerdict(BaseModel):
    verdict: Verdict
    accepted: int
    quarantined: int
    rejected: int
    issues: list[QualityIssue]
    schema_version: Literal["v1"] = SCHEMA_VERSION


class IngestCandlesCommand(BaseModel):
    """Input for `ingest_candles` (LA-15). `tenant_id=None` means platform-shared data."""

    tenant_id: UUID | None
    venue: Venue
    canonical_symbol: str
    timeframe: Timeframe
    range_start: AwareDatetime
    range_end: AwareDatetime
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class IngestBatchResult(BaseModel):
    """Batch record representation that LA-9's `BatchRepository.create()` writes
    verbatim into `md_ingest_batch` (LA-13, task-615 note). The `md_ingest_batch`
    table LA-11 actually built has `venue`/`instrument_id`/`timeframe`/
    `range_start`/`range_end`/`source`/`request_fingerprint` all NOT NULL, so
    the original definition without these fields could not write to that
    table — the LA-9 port signature (`create(conn, batch: IngestBatchResult)`)
    was kept as-is (task note: do not create a new port) and these fields were
    added to this DTO instead. Per rule 107 §8 ("field additions are minor"),
    the fixture (`test_contracts_schema.py`) was updated together with it."""

    batch_id: UUID
    tenant_id: UUID | None = None
    source: str
    venue: Venue
    instrument_id: UUID
    timeframe: Timeframe
    range_start: AwareDatetime
    range_end: AwareDatetime
    request_fingerprint: str
    verdict: QualityVerdict
    batch_hash: str
    audit_event_id: UUID | None
    stored_range: tuple[AwareDatetime, AwareDatetime] | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class TickIngestBatchResult(BaseModel):  # LA-16a: no timeframe, stored in md_ingest_batch_tick
    batch_id: UUID
    tenant_id: UUID | None = None
    source: str
    venue: Venue
    instrument_id: UUID
    range_start: AwareDatetime
    range_end: AwareDatetime
    request_fingerprint: str
    verdict: QualityVerdict
    batch_hash: str
    audit_event_id: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CandleQuery(BaseModel):
    key: SeriesKey
    start: AwareDatetime
    end: AwareDatetime
    as_of: AwareDatetime | None = None
    adjustment: Adjustment = Adjustment.RAW
    include_quarantined: bool = False
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CandleSeries(BaseModel):
    key: SeriesKey
    candles: list[CandleRecord]
    gaps: list[tuple[AwareDatetime, AwareDatetime]]
    adjustment: Adjustment
    as_of: AwareDatetime
    series_hash: str
    schema_version: Literal["v1"] = SCHEMA_VERSION


class ReplayRequest(CandleQuery):
    """Backtest determinism requirement (A5): `as_of` is Optional in the parent
    but redefined as required here, and `include_quarantined` is always fixed to `False`."""

    as_of: AwareDatetime
    include_quarantined: Literal[False] = False


class ReplaySeries(CandleSeries):
    expected_count: int
    missing_count: int


class SessionWindow(BaseModel):
    open_at: AwareDatetime
    close_at: AwareDatetime
    kind: Literal["REGULAR", "EARLY_CLOSE", "CONTINUOUS"]
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CalendarDay(BaseModel):
    venue: Venue
    trade_date: date
    is_trading_day: bool
    open_at: AwareDatetime | None
    close_at: AwareDatetime | None
    early_close: bool = False
    source: str
    schema_version: Literal["v1"] = SCHEMA_VERSION


class InstrumentRef(BaseModel):
    instrument_id: UUID
    venue: Venue
    canonical_symbol: str
    venue_symbol: str
    asset_class: AssetClass
    base: str | None
    quote: str | None
    tick_size: Decimal
    lot_size: Decimal
    status: SymbolStatus
    listed_at: AwareDatetime
    delisted_at: AwareDatetime | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class RegisterInstrumentCommand(BaseModel):
    venue: Venue
    venue_symbol: str
    asset_class: AssetClass
    tick_size: Decimal
    lot_size: Decimal
    listed_at: AwareDatetime
    actor_subject_id: UUID
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class LifecycleEventCommand(BaseModel):
    instrument_id: UUID
    event: Literal["LIST", "SUSPEND", "RESUME", "DELIST", "RENAME"]
    effective_at: AwareDatetime
    new_venue_symbol: str | None = None
    source_ref: str
    actor_subject_id: UUID
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CorporateAction(BaseModel):
    """`ratio`: 2 for a 2:1 split. For dividends, ratio=1 and `cash_amount`
    holds the amount separately.

    `known_at` (RD-20): the time we learned this fact (the filing's receipt
    time) — distinct from `ex_date` (the effective date). A correcting
    filing is appended as a new row with the same `(instrument_id,
    action_type, ex_date)` but a different `known_at` (no UPDATE). The
    existing LA-12/LA-14 paths (ledger recording) leave this field unset
    (`None`) — per rule 107, this is backward compatible since it's an
    added field with a default value."""

    action_type: Literal["SPLIT", "REVERSE_SPLIT", "CASH_DIVIDEND", "MERGER"]
    instrument_id: UUID
    ex_date: date
    ratio: Decimal
    cash_amount: Decimal | None = None
    source_ref: str
    known_at: AwareDatetime | None = None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class DataQualityMetrics(BaseModel):
    key: SeriesKey
    staleness_s: int
    gap_ratio_24h: Decimal
    reject_ratio_24h: Decimal
    last_batch_id: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION
