"""LA-1 — 시장데이터(market_data) 계약 v1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1,
107_contract_versioning_and_compatibility_standard_v1.0.md.

이 모듈은 캔들/틱 수집·품질판정·참조데이터·리플레이의 유일한 공개 표면이다.
`domain/`은 이 파일을 import하지만, 이 파일은 `domain/`을 import하지 않는다
(71번 §4, FND-03·LB-1·LC-1과 동일 원칙). 필드 추가는 minor(107번, 기본값
필수) — 제거·의미 변경은 `v2` 모듈 신설.

모든 `datetime` 필드는 `AwareDatetime`으로 naive 값을 거부한다(tz-naive는
거래소 응답을 잘못 해석했다는 신호이지 정상 입력이 아니다). 가격·수량은
`Decimal`(NUMERIC(30,10)과 동일 정밀도, float 금지).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.data.models.base import AssetClass

SCHEMA_VERSION: Literal["v1"] = "v1"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class Venue(str, Enum):
    """세션 규칙(A3) 조회 키. KIS는 시장별로 캘린더가 달라 KRX/US를 분리한다."""

    BITGET = "BITGET"
    KIS_KRX = "KIS_KRX"
    KIS_US = "KIS_US"


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


class SeriesKey(BaseModel):
    """캔들/틱 시계열 식별자(venue, instrument, timeframe)."""

    venue: Venue
    instrument_id: UUID
    timeframe: Timeframe
    schema_version: Literal["v1"] = SCHEMA_VERSION


class CandleRecord(BaseModel):
    """가격·수량은 NUMERIC(30,10) 저장과 동일 정밀도로 Decimal 유지."""

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
    """`ingest_candles`(LA-15)의 입력. `tenant_id=None`은 플랫폼 공용 데이터."""

    tenant_id: UUID | None
    venue: Venue
    canonical_symbol: str
    timeframe: Timeframe
    range_start: AwareDatetime
    range_end: AwareDatetime
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class IngestBatchResult(BaseModel):
    batch_id: UUID
    verdict: QualityVerdict
    batch_hash: str
    audit_event_id: UUID | None
    stored_range: tuple[AwareDatetime, AwareDatetime] | None
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
    """백테스트 결정론 요구(A5): `as_of`는 부모에서 Optional이지만 여기선
    필수로 재정의하고, `include_quarantined`는 항상 `False`로 고정한다."""

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
    """`ratio`: 2:1 분할이면 2. 배당은 ratio=1, `cash_amount`에 별도 기록."""

    action_type: Literal["SPLIT", "REVERSE_SPLIT", "CASH_DIVIDEND", "MERGER"]
    instrument_id: UUID
    ex_date: date
    ratio: Decimal
    cash_amount: Decimal | None = None
    source_ref: str
    schema_version: Literal["v1"] = SCHEMA_VERSION


class DataQualityMetrics(BaseModel):
    key: SeriesKey
    staleness_s: int
    gap_ratio_24h: Decimal
    reject_ratio_24h: Decimal
    last_batch_id: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION
