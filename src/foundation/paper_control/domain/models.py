"""Paper Execution & Control domain model — pure value object.

Spec: AIOSproject 77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class DeploymentState(str, Enum):
    REQUESTED = "REQUESTED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    RECOVERY_REVIEW = "RECOVERY_REVIEW"


class CredentialClass(str, Enum):
    """77 §1 "credential_class=PAPER" — the only value this context handles.
    Like CredentialClass.READONLY in 74 connections, we do not pre-create
    other values here (LIVE requires separate review after gates 60-63)."""

    PAPER = "PAPER"


class CommandOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    DENIED = "DENIED"


class CommandType(str, Enum):
    REQUEST = "REQUEST"
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


@dataclass(frozen=True)
class AdapterProvenance:
    """77 §1 "A boolean is_paper alone is insufficient" — requires structured
    provenance. This leaf only has a fake adapter so adapter_type is
    effectively constant, but we keep the fields structured for when real
    adapters are added."""

    adapter_type: str
    credential_class: CredentialClass
    endpoint_classification: str
    provider_sandbox_account_ref: str


@dataclass(frozen=True)
class PaperDeployment:
    id: UUID
    tenant_id: UUID
    connection_id: UUID | None
    package_ref: str
    mandate_revision_id: UUID
    provenance: AdapterProvenance
    state: DeploymentState
    fence_token: int
    request_idempotency_key: str | None = None
    request_digest: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class DeploymentCommand:
    id: UUID
    deployment_id: UUID
    idempotency_key: str
    command_type: CommandType
    actor_subject_id: UUID
    outcome: CommandOutcome
    detail: str | None
    created_at: datetime | None = None


@dataclass(frozen=True)
class PaperOrderIntent:
    id: UUID
    deployment_id: UUID
    sequence: int
    fence_token_at_submit: int
    state: str
    created_at: datetime | None = None
