"""Audit Event 계약 v1.

Spec: AIOSproject 49_audit_evidence_and_explainability_specification_v1.0.md,
79_audit_evidence_l3_build_and_operational_specification_v1.0.md §1/§3,
107_contract_versioning_and_compatibility_standard_v1.0.md.

다른 bounded context(FND-01/02 등)는 이 파일을 통해서만 감사 이벤트를
기록·조회한다 — domain/models.py를 직접 참조하지 않는다(71번 §4).
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
    """AppendAuditEvent의 입력. `tenant_id`가 None이면 system 이벤트(79번 §1).
    `payload`는 78번(AUD-004) 안전성 검사를 통과해야 한다 — secret류 키가
    있으면 domain.rules.assert_safe_payload()가 UnsafePayloadError를 던진다."""

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
    """79번 §3 "opaque cursor, time range, aggregate/action filter and
    maximum bounded page" — `next_cursor`가 None이면 더 없음."""

    items: list[AuditEventView]
    next_cursor: str | None
    as_of: datetime
