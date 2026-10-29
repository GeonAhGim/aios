"""Audit Event domain model — pure value object.

Spec: AIOSproject 79_audit_evidence_l3_build_and_operational_specification_v1.0.md §1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"
    ERROR = "ERROR"


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET_REFERENCE = "SECRET_REFERENCE"


@dataclass(frozen=True)
class AuditEvent:
    """79 §1 `audit_event` — append-only, per-aggregate sequence unique.

    If `tenant_id` is None, this is a system event (as per §1). The chain is
    independent per tenant — `sequence_no`/`previous_hash`/`event_hash` are
    meaningful only within that tenant's (or system's) chain."""

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
