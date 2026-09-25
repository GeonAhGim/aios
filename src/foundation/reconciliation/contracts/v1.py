"""Reconciliation & Resilience 계약 v1.

Spec: AIOSproject 50_reconciliation_resilience_specification_v1.0.md,
80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md §3,
107_contract_versioning_and_compatibility_standard_v1.0.md.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class Classification(str, Enum):
    HEALTHY = "HEALTHY"
    PENDING = "PENDING"
    MINOR_DIFFERENCE = "MINOR_DIFFERENCE"
    MATERIAL_MISMATCH = "MATERIAL_MISMATCH"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"


class EntitySnapshot(BaseModel):
    """Per §80 §1 "typed snapshots" — one pair of internal/provider values
    per entity_key (e.g. "USDT_BALANCE", "BTCUSDT_POSITION"). A missing
    provider_value (None) means the provider did not return that item at all
    — do not interpret it as zero (§2)."""

    entity_type: str
    entity_key: str
    internal_value: Decimal
    provider_value: Decimal | None = None


class RunReconciliationRequest(BaseModel):
    target_type: str
    target_ref: UUID
    connection_id: UUID | None = None
    entities: list[EntitySnapshot]


class ReconciliationItemView(BaseModel):
    entity_type: str
    entity_key: str
    internal_value: Decimal
    provider_value: Decimal | None
    classification: Classification


class ReconciliationRunView(BaseModel):
    id: UUID
    target_type: str
    target_ref: UUID
    items: list[ReconciliationItemView]
    aggregate_classification: Classification
    created_at: datetime | None
    schema_version: str = SCHEMA_VERSION


class ResolveReconciliationRequest(BaseModel):
    reason: str


class ReconciliationStateView(BaseModel):
    target_ref: UUID
    target_type: str
    aggregate_status: Classification
    last_healthy_at: datetime | None
    last_checked_at: datetime
    blocking_reason: str | None
    revision: int
    schema_version: str = SCHEMA_VERSION
