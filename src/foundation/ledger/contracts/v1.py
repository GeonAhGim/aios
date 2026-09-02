"""LC-1 — 머니 원장(ledger) 계약 v1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3 (C), §9 LC-1,
107_contract_versioning_and_compatibility_standard_v1.0.md.

이 모듈은 원장 포스팅의 유일한 공개 표면이다. `domain/`은 이 파일을
import하지만, 이 파일은 `domain/`을 import하지 않는다(71번 §4, FND-03과
동일 원칙). 필드 추가는 minor(107번, 기본값 필수) — 제거·의미 변경은
`v2` 모듈 신설.

`AccountCode`는 별도 클래스가 아니라 `str` 형식 규약이다(§3.3):
"USER:{uuid}:{UserSub}" | "PLATFORM:{NAME}". 형식 검증·발급은
`domain/chart_of_accounts.py`(LC-2)의 책임이며 계약 계층에서는 강제하지
않는다 — 계약은 domain을 모르기 때문이다.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from src.data.models.base import Currency

SCHEMA_VERSION: Literal["v1"] = "v1"


class AccountType(str, Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    CLEARING = "CLEARING"


class UserSub(str, Enum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    PENDING_PAYOUT = "PENDING_PAYOUT"
    RECEIVABLE = "RECEIVABLE"


class Side(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class LedgerEventType(str, Enum):
    TOPUP_CONFIRMED = "TOPUP_CONFIRMED"
    HOLD_PLACED = "HOLD_PLACED"
    HOLD_CAPTURED = "HOLD_CAPTURED"
    HOLD_RELEASED = "HOLD_RELEASED"
    REFUND = "REFUND"
    CHARGEBACK = "CHARGEBACK"
    PAYOUT_RELEASE = "PAYOUT_RELEASE"
    PAYOUT_PAID = "PAYOUT_PAID"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


class HoldState(str, Enum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class LedgerEvent(BaseModel):
    """`post_entry`(LC-9)의 입력. `event_ref`는 "purchase:123" 형태의
    도메인 참조이며, `idempotency_key`(LC-3)는 이로부터 파생된다."""

    event_type: LedgerEventType
    event_ref: str
    tenant_id: UUID | None
    actor_subject_id: UUID | None
    trace_id: UUID
    amount: Decimal = Field(gt=0)
    currency: Currency = Currency.KRW
    parties: dict[str, UUID]
    extra: dict[str, Decimal | str] = {}
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PostingLine(BaseModel):
    """분개 행 하나. `amount`는 항상 양수 — 방향은 `side`가 표현한다
    (§3.3 원장 금액 NUMERIC(20,2) KRW quantize)."""

    line_no: int
    account_code: str
    side: Side
    amount: Decimal = Field(gt=0, decimal_places=2)
    currency: Currency


class JournalEntryView(BaseModel):
    entry_id: UUID
    sequence_no: int
    event_type: LedgerEventType
    event_ref: str
    idempotency_key: str
    lines: list[PostingLine]
    lines_digest: str
    prev_hash: str | None
    entry_hash: str
    audit_event_id: UUID
    posted_at: AwareDatetime
    replayed: bool = False
    schema_version: Literal["v1"] = SCHEMA_VERSION


class BalanceView(BaseModel):
    account_code: str
    balance: Decimal
    held: Decimal
    available: Decimal
    pending_payout: Decimal
    currency: Currency
    last_entry_seq: int
    as_of: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class HoldView(BaseModel):
    hold_id: UUID
    account_code: str
    amount: Decimal
    purpose: str
    reference: str
    state: HoldState
    expires_at: AwareDatetime
    entry_id: UUID
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PayoutBatchView(BaseModel):
    batch_id: UUID
    seller_user_id: UUID
    period_start: date
    period_end: date
    amount: Decimal
    state: Literal["SCHEDULED", "RELEASED", "PAID", "FAILED"]
    capture_entry_ids: list[UUID]
    release_entry_id: UUID | None
    paid_entry_id: UUID | None
    schema_version: Literal["v1"] = SCHEMA_VERSION


class TrialBalanceView(BaseModel):
    """`total`은 복식부기 항등식에 의해 항상 0이어야 한다(LC-5, §4.4)."""

    as_of: AwareDatetime
    last_entry_seq: int
    balances: dict[str, Decimal]
    total: Decimal
    schema_version: Literal["v1"] = SCHEMA_VERSION


class IntegrityReport(BaseModel):
    checked_at: AwareDatetime
    entries_verified: int
    chain_ok: bool
    zero_sum_ok: bool
    drifts: list[tuple[str, Decimal, Decimal]]
    first_broken_seq: int | None
    schema_version: Literal["v1"] = SCHEMA_VERSION
