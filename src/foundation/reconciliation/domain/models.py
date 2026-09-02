"""Reconciliation & Resilience 도메인 모델 — pure value object.

Spec: AIOSproject 80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class Classification(str, Enum):
    """80번 §1 7개 분류 전부 — item(레코드 하나)과 run/state(집계) 양쪽에서
    같은 값 집합을 공유한다(스펙 원문 그대로)."""

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
    """REC-004/006 — 같은 (target_ref, input_hash)로 이미 실행된 run이
    있으면 새로 계산하지 않고 이 상태로 기존 run을 가리킨다."""


@dataclass(frozen=True)
class MaterialityPolicy:
    """80번 §1 "typed/asset-aware policy, not UI configuration" — 이 리프는
    entity_type별로 다른 정책을 주입받을 수 있는 형태만 갖추고(딕셔너리),
    실제 자산별 세분화된 정책 테이블은 만들지 않는다(마이그레이션 docstring
    스콥 축소)."""

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
