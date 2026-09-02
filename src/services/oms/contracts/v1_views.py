"""OMS 읽기 모델(L4 명세 §3.1, 2-B).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-B, §9 L4-01.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.services.oms.contracts.v1_events import Discrepancy, FillEvent, OrderTransitionEvent

SCHEMA_VERSION = "v1"


class OrderView(BaseModel):
    order_id: UUID
    tenant_id: UUID
    execution_id: int | None
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    venue_symbol: str | None
    exchange: str
    side: OrderSide
    order_type: OrderType
    time_in_force: str
    quantity: Decimal
    price: Decimal | None
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    fee_total: Decimal | None
    fee_currency: str | None
    version: int
    parent_order_id: UUID | None
    algo_run_id: UUID | None
    unknown_since: datetime | None
    provider_order_date: date | None
    created_at: datetime
    updated_at: datetime


class OrderTimelineView(BaseModel):
    order: OrderView
    events: list[OrderTransitionEvent]
    fills: list[FillEvent]


class AlgoRunView(BaseModel):
    algo_run_id: UUID
    algo: str
    symbol: str
    side: OrderSide
    total_quantity: Decimal
    filled_quantity: Decimal
    state: str  # PENDING | RUNNING | PAUSED | COMPLETED | CANCELLED
    slice_count: int
    slices_submitted: int
    created_at: datetime
    updated_at: datetime


class ReconcileSummaryView(BaseModel):
    account_ref: str
    window_start: datetime
    window_end: datetime
    discrepancies: list[Discrepancy]
    overall_classification: str
    checked_at: datetime
