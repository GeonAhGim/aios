"""Paper Execution & Control 계약 v1.

Spec: AIOSproject 47_paper_execution_and_control_center_specification_v1.0.md,
77_paper_execution_control_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class DeploymentState(str, Enum):
    REQUESTED = "REQUESTED"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    DEGRADED = "DEGRADED"
    RECOVERY_REVIEW = "RECOVERY_REVIEW"


class RequestDeploymentRequest(BaseModel):
    package_ref: str
    connection_id: UUID | None = None
    adapter_type: str
    provider_sandbox_account_ref: str
    endpoint_classification: str = "SANDBOX"
    idempotency_key: str


class DeploymentCommandRequest(BaseModel):
    idempotency_key: str


class PaperDeploymentView(BaseModel):
    id: UUID
    package_ref: str
    connection_id: UUID | None
    state: DeploymentState
    fence_token: int
    created_at: datetime | None
    updated_at: datetime | None
    schema_version: str = SCHEMA_VERSION
