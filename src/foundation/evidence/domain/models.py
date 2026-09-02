"""Audit Event 도메인 모델 — pure value object.

Spec: AIOSproject 79_audit_evidence_l3_build_and_operational_specification_v1.0.md §1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


class Classification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET_REFERENCE = "SECRET_REFERENCE"


@dataclass(frozen=True)
class AuditEvent:
    """79번 §1 `audit_event` — append-only, per-aggregate sequence unique.

    `tenant_id`가 None이면 system 이벤트(79번 §1 그대로)다. 체인은 tenant별로
    독립적이다 — `sequence_no`/`previous_hash`/`event_hash`가 그 tenant(또는
    system)의 체인 안에서만 의미를 갖는다."""

    id: UUID
    tenant_id: UUID | None
    sequence_no: int
    aggregate_type: str
    aggregate_id: UUID
    aggregate_revision: int | None
    action: str
    outcome: Outcome
    actor_subject_id: UUID | None
    trace_id: UUID
    payload_hash: str
    payload: dict[str, Any] = field(default_factory=dict)
    classification: Classification = Classification.INTERNAL
    previous_hash: str | None = None
    event_hash: str = ""
    occurred_at: datetime | None = None
