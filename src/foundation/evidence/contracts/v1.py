"""Audit event contract v1.

Spec: AIOSproject 49_audit_evidence_and_explainability_specification_v1.0.md,
79_audit_evidence_l3_build_and_operational_specification_v1.0.md §1/§3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

Other bounded contexts (FND-01/02, etc.) record and query audit events
only through this file — they must not reference domain/models.py directly (71st §4).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


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


class RecordAuditEventCommand(BaseModel):
    """Input for AppendAuditEvent. If `tenant_id` is None, this is a system event (79th §1).
    `payload` must pass the safety check in 78th (AUD-004) — if it contains secret-like keys,
    domain.rules.assert_safe_payload() raises UnsafePayloadError."""

    tenant_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    aggregate_revision: int | None = None
    action: str
    outcome: Outcome
    actor_subject_id: UUID | None = None
    trace_id: UUID
    payload: dict[str, Any] = {}
    classification: Classification = Classification.INTERNAL


class AuditEventView(BaseModel):
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
    payload: dict[str, Any]
    classification: Classification
    previous_hash: str | None
    event_hash: str
    occurred_at: datetime
    schema_version: str = SCHEMA_VERSION


class AuditTimelinePage(BaseModel):
    """79th §3 "opaque cursor, time range, aggregate/action filter and
    maximum bounded page" — if `next_cursor` is None, there are no more pages."""

    items: list[AuditEventView]
    next_cursor: str | None
    as_of: datetime
