"""OMS 이벤트 DTO(L4 명세 §3.1).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.1, §9 L4-01.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from src.data.models.trading import OrderSide, OrderStatus
from src.foundation.reconciliation.contracts.v1 import Classification

SCHEMA_VERSION = "v1"


class FillEvent(BaseModel):
    provider_fill_id: str
    venue: str
    order_id: UUID | None
    exchange_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_currency: str
    liquidity: Literal["MAKER", "TAKER", "UNKNOWN"]
    venue_ts: datetime


class FillAggregate(BaseModel):
    filled_qty: Decimal
    avg_price: Decimal
    fee_total: dict[str, Decimal]  # currency -> 합계(107번 §3 MINOR — dict라 필드 추가 불필요)


class ProviderOrderEvent(BaseModel):
    provider_event_id: str  # venue 고유(WS seq|fill id|"poll:{order}:{uTime}")
    venue: str
    venue_symbol: str
    exchange_order_id: str | None
    client_order_id: str | None
    venue_status: str
    filled_quantity: Decimal
    average_price: Decimal | None
    last_fill: FillEvent | None
    venue_ts: datetime
    received_at: datetime
    source: Literal["WS", "POLL", "RESYNC", "SUBMIT_RESPONSE"]
    raw_hash: str  # raw 본문은 저장하지 않는다(108번 §2.1)


class OrderTransitionEvent(BaseModel):
    order_id: UUID
    seq: int | None = None
    from_status: OrderStatus
    to_status: OrderStatus
    event: str  # OrderEvent.value
    reason_code: str | None
    actor_subject_id: UUID | Literal["system"]
    trace_id: UUID
    command_id: UUID | None
    provider_event_id: str | None
    occurred_at: datetime
    payload_hash: str


class Discrepancy(BaseModel):
    kind: Literal[
        "ORDER_MISSING_AT_PROVIDER",
        "ORDER_MISSING_INTERNAL",
        "STATUS_MISMATCH",
        "FILLED_QTY_MISMATCH",
        "FILL_MISSING_INTERNAL",
        "BALANCE_MISMATCH",
    ]
    entity_key: str
    internal_value: Decimal | str | None
    provider_value: Decimal | str | None
    materiality: Classification
