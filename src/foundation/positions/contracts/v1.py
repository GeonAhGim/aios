"""LB-1 — 포지션/PnL 원장(positions) 계약 v1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2 (B), §9 LB-1,
107_contract_versioning_and_compatibility_standard_v1.0.md.

이 모듈은 포지션 저널·스냅샷·PnL·NAV의 유일한 공개 표면이다.
`domain/`은 이 파일을 import하지만, 이 파일은 `domain/`을 import하지
않는다(71번 §4, FND-03·LC-1과 동일 원칙). 필드 추가는 minor(107번, 기본값
필수) — 제거·의미 변경은 `v2` 모듈 신설.

금액·수량은 원시 `Decimal`이 아니라 `Money`(계좌 통화)로 표현하되,
기준통화 환산값(`*_base` 필드)은 `Decimal`이다 — §3.4 "PnL 기준통화
금액은 NUMERIC(30,10) 저장, 절대 반올림하지 않음"과 짝을 이룬다. 모든
`datetime` 필드는 `AwareDatetime`으로 naive 값을 거부한다(tz-naive는
거래소 응답을 잘못 해석했다는 신호이지 정상 입력이 아니다).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency, FXRate, Money
from src.data.models.trading import OrderSide

SCHEMA_VERSION: Literal["v1"] = "v1"


class CostMethod(str, Enum):
    FIFO = "FIFO"
    WEIGHTED = "WEIGHTED"


class JournalEntryType(str, Enum):
    FILL = "FILL"
    FUNDING = "FUNDING"
    FEE = "FEE"
    ADJUSTMENT = "ADJUSTMENT"
    CORP_ACTION = "CORP_ACTION"


class PositionErrorCode(str, Enum):
    """§3.2 에러 taxonomy(B) 8종. 각 값의 재시도 가능성·호출자 조치는
    스펙 본문을 그대로 옮긴 주석을 참고한다 — 이 계약 파일은 코드만
    정의하고, 실제 예외 클래스는 이를 사용하는 domain 리프(LB-2 이후)의
    책임이다."""

    IDEMPOTENT_REPLAY = "POS_IDEMPOTENT_REPLAY"  # 오류 아님, 기존 뷰 반환
    IDEMPOTENCY_DIGEST_MISMATCH = "POS_IDEMPOTENCY_DIGEST_MISMATCH"  # 불가, 호출자 버그
    SEQUENCE_CONFLICT = "POS_SEQUENCE_CONFLICT"  # 가능, 재조회 후 재시도
    NEGATIVE_QUANTITY = "POS_NEGATIVE_QUANTITY"  # 불가, 현물 공매도 금지 — 주문 경로 버그
    FX_RATE_MISSING = "POS_FX_RATE_MISSING"  # 가능, 환율 도착 후 — 0으로 대체 금지
    MARK_STALE = "POS_MARK_STALE"  # 가능, 미실현은 None 유지
    NAV_CHAIN_BROKEN = "POS_NAV_CHAIN_BROKEN"  # 불가, 운영 개입
    ACCOUNT_UNKNOWN = "POS_ACCOUNT_UNKNOWN"  # 불가


class RecordFillCommand(BaseModel):
    """`record_fill`(LB-11)의 입력. 멱등키는 `f"fill:{order_id}:{fill_seq}"`
    (§5 저널 append 멱등성)."""

    tenant_id: UUID
    account_id: UUID
    position_key: str
    order_id: UUID
    fill_seq: int
    side: OrderSide
    quantity: Decimal
    price: Money
    fee: Money | None
    contract_multiplier: Decimal = Decimal("1")
    occurred_at: AwareDatetime
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class RecordFundingCommand(BaseModel):
    """`record_funding_fee`(LB-13)의 입력. 멱등키는
    `f"funding:{funding_id}"`."""

    tenant_id: UUID
    account_id: UUID
    position_key: str
    funding_id: str
    amount: Money
    rate: Decimal
    occurred_at: AwareDatetime
    trace_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PositionJournalEntryView(BaseModel):
    """append-only `pos_journal` 행 하나의 뷰(§4.3 저널 불변조건)."""

    id: int
    position_key: str
    sequence_no: int
    entry_type: JournalEntryType
    qty_delta: Decimal
    price: Money | None
    fee: Money | None
    realized_pnl_base: Decimal
    fx_rate: Decimal | None
    fx_source: str | None
    source_event_type: str
    source_event_id: str
    idempotency_key: str
    prev_hash: str | None
    entry_hash: str
    occurred_at: AwareDatetime
    recorded_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class Lot(BaseModel):
    """원가법 로트 하나(FIFO/WEIGHTED 공통 표현, LB-2/LB-3 소비)."""

    quantity: Decimal
    unit_cost: Decimal
    opened_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PositionSnapshotView(BaseModel):
    """저널의 fold 결과(§4.3 "스냅샷 = fold(저널)"). 미실현 PnL은 마크
    없으면 `None`(0 아님)."""

    position_key: str
    tenant_id: UUID
    account_id: UUID
    instrument_id: UUID
    quantity: Decimal
    avg_cost: Money
    cost_method: CostMethod
    lots: list[Lot]
    realized_pnl_base: Decimal
    unrealized_pnl_base: Decimal | None
    fees_base: Decimal
    funding_base: Decimal
    mark_price: Money | None
    mark_at: AwareDatetime | None
    base_currency: Currency
    last_journal_seq: int
    updated_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PnLBreakdown(BaseModel):
    realized: Decimal
    unrealized: Decimal
    fees: Decimal
    funding: Decimal
    total: Decimal
    base_currency: Currency
    fx_rates_used: list[FXRate]
    schema_version: Literal["v1"] = SCHEMA_VERSION


class NAVSnapshot(BaseModel):
    """일별 NAV 체인 한 행(§4.3 "전일 NAV + 손익 + 자금흐름 = 당일 NAV",
    DB `CHECK(closing_nav = cash + positions_mv)`)."""

    account_id: UUID
    nav_date: date
    base_currency: Currency
    opening_nav: Decimal
    cash: Decimal
    positions_mv: Decimal
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal
    closing_nav: Decimal
    fx_rates: list[FXRate]
    source_hash: str
    schema_version: Literal["v1"] = SCHEMA_VERSION


class RebuildReport(BaseModel):
    """`rebuild_snapshot`(LB-13) 결과 — 재빌드 전후 값이 다른 필드만
    `drift`에 `(old, new)`로 기록한다(§4.3 재빌드 drift 검증)."""

    position_key: str
    entries: int
    drift: dict[str, tuple[Decimal, Decimal]]
    applied: bool
    schema_version: Literal["v1"] = SCHEMA_VERSION
