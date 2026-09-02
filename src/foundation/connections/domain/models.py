"""Connected Asset(읽기전용 계좌 연결) 도메인 모델 — pure value object.

Spec: AIOSproject 74_connected_asset_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


class ConnectionState(str, Enum):
    PENDING_CONSENT = "PENDING_CONSENT"
    CONNECTING = "CONNECTING"
    ACTIVE_READONLY = "ACTIVE_READONLY"
    DEGRADED = "DEGRADED"
    REVOKED = "REVOKED"
    DISCONNECTED = "DISCONNECTED"


class CapabilityScope(str, Enum):
    """74번 §1 "P0 capability profile is a closed enum set" — 이 세 값 외에는
    전부 하드 거부 대상이다(TRADE_*, WITHDRAW, TRANSFER, SIGN_* 포함).
    실제 열거값이 아니라 임의 문자열이 들어올 수 있는 입력 경로(요청 바디)는
    rules.validate_capability_profile()이 이 enum으로 변환하며 걸러낸다."""

    READ_BALANCE = "READ_BALANCE"
    READ_POSITION = "READ_POSITION"
    READ_ACTIVITY = "READ_ACTIVITY"


class CredentialClass(str, Enum):
    """74번 §1 "credential class must be READONLY" — P0에서 이 enum이 갖는
    유일한 합법값은 READONLY뿐이다. 다른 값을 미리 만들어두지 않는다(35번
    §9.2 "미리 만들어두지 않는다" 원칙 — 실제 TRADE 자격증명이 필요해지면
    그때 별도 검토를 거쳐 추가한다)."""

    READONLY = "READONLY"


@dataclass(frozen=True)
class AccountConnection:
    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    provider_code: str
    opaque_account_ref: str
    state: ConnectionState
    capability_profile: tuple[CapabilityScope, ...]
    revision: int
    created_at: datetime | None = None


@dataclass(frozen=True)
class CredentialBinding:
    id: UUID
    connection_id: UUID
    vault_secret_ref: str
    scope_fingerprint: str
    credential_class: CredentialClass
    expires_at: datetime | None
    rotation_state: str = "CURRENT"


@dataclass(frozen=True)
class ConnectionConsent:
    """74번 §1 `connection_consent` — 이 connection이 Trust Core(FND-01)의
    어느 동의 레코드를 근거로 활성화됐는지 가리키는 포인터 테이블이다.
    동의 자체의 신선도/철회 판정은 Trust가 소유하며(71번 §4), 이 테이블은
    "이 connection은 그 시점에 어떤 동의를 근거로 삼았는가"만 기록한다."""

    connection_id: UUID
    consent_ref: UUID
    data_purposes: tuple[str, ...]
    expires_at: datetime | None


@dataclass(frozen=True)
class AccountSnapshot:
    id: UUID
    connection_id: UUID
    captured_at: datetime
    provider_as_of: datetime
    freshness: str
    currency: str
    source_evidence_ref: str


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class ConnectionHealth:
    connection_id: UUID
    evaluated_at: datetime
    state: HealthState
    error_code: str | None = None
    retry_after: datetime | None = None
    provider_trace_ref: str | None = None


@dataclass(frozen=True)
class ScopeProof:
    """74번 §3 ReadonlyAccountProvider.verify_readonly_scope()의 반환값 —
    provider가 실제로 승인한 스코프(요청과 다를 수 있음, "scope drift")."""

    granted_scopes: tuple[CapabilityScope, ...]
    provider_credential_ref: str


@dataclass(frozen=True)
class ProviderSnapshot:
    """74번 §3 ReadonlyAccountProvider.fetch_snapshot()의 반환값."""

    provider_as_of: datetime
    currency: str
    raw_payload_ref: str = field(default="")
