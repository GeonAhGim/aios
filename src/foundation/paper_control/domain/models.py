"""Paper Execution & Control 도메인 모델 — pure value object.

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
    """77번 §1 "credential_class=PAPER" — 이 컨텍스트가 다룰 수 있는 유일한
    값. 74번 connections의 CredentialClass.READONLY와 같은 이유로 다른
    값을 미리 만들어두지 않는다(LIVE는 60~63번 승인 게이트 이후 별도 검토)."""

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
    """77번 §1 "A boolean is_paper alone is insufficient" — 구조화된 근거를
    요구한다. 이 리프는 fake adapter 하나만 있으므로 adapter_type이 사실상
    상수지만, 필드 자체는 실 adapter가 추가될 때를 대비해 구조를 유지한다."""

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
