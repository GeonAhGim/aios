"""Reconciliation & Resilience domain model — pure value object.

Spec: AIOSproject 80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class Classification(str, Enum):
    """All 7 classifications from §1 of spec 80 — shares the same value set
    across both item (single record) and run/state (aggregate) contexts
    (verbatim from the spec)."""

    HEALTHY = "HEALTHY"
    PENDING = "PENDING"
    MINOR_DIFFERENCE = "MINOR_DIFFERENCE"
    MATERIAL_MISMATCH = "MATERIAL_MISMATCH"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"


class RunState(str, Enum):
    COMPLETED = "COMPLETED"
    DEDUPED = "DEDUPED"
    """REC-004/006 — When a run with the same (target_ref, input_hash)
    has already been executed, skip recalculation and point to the
    existing run instead."""


@dataclass(frozen=True)
class MaterialityPolicy:
    """80 §1 "typed/asset-aware policy, not UI configuration" — this leaf
    only supports injection of different policies per entity_type
    (via a dictionary); it does not create actual asset-granular
    policy tables (migration docstring scope reduced)."""

    absolute_tolerance: Decimal
    relative_tolerance_pct: Decimal


@dataclass(frozen=True)
class ReconciliationItem:
    id: UUID
    run_id: UUID
    entity_type: str
    entity_key: str
    internal_value: Decimal
    provider_value: Decimal | None
    classification: Classification
    created_at: datetime | None = None


@dataclass(frozen=True)
class ReconciliationRun:
    id: UUID
    tenant_id: UUID
    target_type: str
    target_ref: UUID
    connection_id: UUID | None
    input_hash: str
    state: RunState
    rule_version: str
    items: tuple[ReconciliationItem, ...] = field(default_factory=tuple)
    created_at: datetime | None = None


@dataclass(frozen=True)
class ReconciliationState:
    target_ref: UUID
    target_type: str
    tenant_id: UUID
    aggregate_status: Classification
    last_healthy_at: datetime | None
    last_checked_at: datetime
    blocking_reason: str | None
    revision: int
    safety_control_id: UUID | None
    resolved_by: UUID | None = None
    resolution_reason: str | None = None
    resolved_at: datetime | None = None
