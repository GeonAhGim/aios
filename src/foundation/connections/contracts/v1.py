"""Connected Asset 계약 v1.

Spec: AIOSproject 44_connected_asset_readonly_connection_specification_v1.0.md,
74_connected_asset_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.

다른 bounded context(mandates/risk_gate/reconciliation)는 이 파일을 소비하고,
domain/models.py를 직접 참조하지 않는다(71번 §4, 106번 §5).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class ConnectionState(str, Enum):
    PENDING_CONSENT = "PENDING_CONSENT"
    CONNECTING = "CONNECTING"
    ACTIVE_READONLY = "ACTIVE_READONLY"
    DEGRADED = "DEGRADED"
    REVOKED = "REVOKED"
    DISCONNECTED = "DISCONNECTED"


class CapabilityScope(str, Enum):
    READ_BALANCE = "READ_BALANCE"
    READ_POSITION = "READ_POSITION"
    READ_ACTIVITY = "READ_ACTIVITY"


class BeginConnectionRequest(BaseModel):
    provider_code: str
    opaque_account_ref: str
    requested_capability_profile: list[CapabilityScope]


class AccountConnectionView(BaseModel):
    """74번 §4 "returns masked provider/account label, allowed capability,
    state, last successful sync, freshness" — provider access token/secret은
    필드 자체가 존재하지 않는다(반환할 수 있는 게 없음)."""

    id: UUID
    provider_code: str
    masked_account_label: str
    state: ConnectionState
    capability_profile: list[CapabilityScope]
    revision: int
    created_at: datetime | None
    scope_verified: bool = False
    """감사 §6 — provider가 요청 스코프를 독립적으로 확인해줬는지. 실
    provider(거래소) 경로는 항상 False(정직 표기, LiveReadonlyAccountProvider
    참조) — 연결이 비활성이거나 아직 confirm 전이면 False."""
    schema_version: str = SCHEMA_VERSION


class SnapshotValueView(BaseModel):
    entity_type: str
    entity_key: str
    value: Decimal


class AccountSnapshotView(BaseModel):
    connection_id: UUID
    captured_at: datetime
    provider_as_of: datetime
    freshness: str
    currency: str
    values: list[SnapshotValueView] = []
    schema_version: str = SCHEMA_VERSION
