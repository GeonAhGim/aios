"""LB-1 — positions/PnL ledger contract v1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2 (B), §9 LB-1,
107_contract_versioning_and_compatibility_standard_v1.0.md.

This module is the sole public surface for the position journal, snapshot,
PnL, and NAV. `domain/` imports this file, but this file does not import
`domain/` (same principle as doc 71 §4, FND-03/LC-1). Adding a field is
minor (doc 107, default required) — removal or a meaning change requires a
new `v2` module.

Amounts and quantities are represented as `Money` (account currency)
rather than a raw `Decimal`, while base-currency conversions (`*_base`
fields) are `Decimal` — this pairs with §3.4 "PnL base-currency amounts
are stored as NUMERIC(30,10) and never rounded." Every `datetime` field is
`AwareDatetime`, rejecting naive values (a tz-naive value signals a
misparsed exchange response, not a valid input).
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
    """The 8 error-taxonomy codes from §3.2 (B). See the spec body for each
    value's retryability and the caller's required action — the comments
    here just carry that text over verbatim. This contract file only
    defines the codes; the actual exception classes are owned by the
    domain leaves that consume them (LB-2 onward)."""

    IDEMPOTENT_REPLAY = "POS_IDEMPOTENT_REPLAY"  # Not an error, returns the existing view
    IDEMPOTENCY_DIGEST_MISMATCH = "POS_IDEMPOTENCY_DIGEST_MISMATCH"  # Not retryable, caller bug
    SEQUENCE_CONFLICT = "POS_SEQUENCE_CONFLICT"  # Retryable, re-fetch and retry
    NEGATIVE_QUANTITY = "POS_NEGATIVE_QUANTITY"  # Not retryable, no spot shorting — order bug
    FX_RATE_MISSING = "POS_FX_RATE_MISSING"  # Retryable once FX rate arrives — never substitute 0
    MARK_STALE = "POS_MARK_STALE"  # Retryable, unrealized stays None
    NAV_CHAIN_BROKEN = "POS_NAV_CHAIN_BROKEN"  # Not retryable, requires ops intervention
    ACCOUNT_UNKNOWN = "POS_ACCOUNT_UNKNOWN"  # Not retryable


class RecordFillCommand(BaseModel):
    """Input for `record_fill` (LB-11). Idempotency key is
    `f"fill:{order_id}:{fill_seq}"` (§5 journal append idempotency)."""

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
    """Input for `record_funding_fee` (LB-13). Idempotency key is
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
    """View of a single append-only `pos_journal` row (§4.3 journal invariants)."""

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
    """A single cost-basis lot (shared FIFO/WEIGHTED representation, consumed by LB-2/LB-3)."""

    quantity: Decimal
    unit_cost: Decimal
    opened_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION


class PositionSnapshotView(BaseModel):
    """The fold result of the journal (§4.3 "snapshot = fold(journal)").
    Unrealized PnL is `None` (not 0) when there is no mark."""

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
    """One row of the daily NAV chain (§4.3 "prior-day NAV + PnL + cash
    flows = current-day NAV", DB `CHECK(closing_nav = cash + positions_mv)`)."""

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
    """Result of `rebuild_snapshot` (LB-13) — only fields whose value
    changed across the rebuild are recorded in `drift` as `(old, new)`
    (§4.3 rebuild drift verification)."""

    position_key: str
    entries: int
    drift: dict[str, tuple[Decimal, Decimal]]
    applied: bool
    schema_version: Literal["v1"] = SCHEMA_VERSION
