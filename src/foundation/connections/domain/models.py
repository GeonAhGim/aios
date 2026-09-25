"""Connected Asset (read-only account connection) domain model — pure value object.

Spec: AIOSproject 74_connected_asset_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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
    """74 §1 "P0 capability profile is a closed enum set" — anything other than
    these three values is a hard reject (including TRADE_*, WITHDRAW, TRANSFER, SIGN_*).
    Input paths that may receive arbitrary strings instead of actual enum values
    (request body) are converted and filtered by rules.validate_capability_profile()."""

    READ_BALANCE = "READ_BALANCE"
    READ_POSITION = "READ_POSITION"
    READ_ACTIVITY = "READ_ACTIVITY"


class CredentialClass(str, Enum):
    """74 §1 "credential class must be READONLY" — the only valid value for
    this enum at P0 is READONLY. Do not pre-create other values (35 §9.2
    "do not pre-create" principle — when actual TRADE credentials are needed,
    add them after separate review)."""

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
    scope_verified: bool = False
    """Audit §6 — whether the provider independently confirmed the actually
    granted scope. The exchange API has no lookup tool that directly maps
    to AIOS's READ_BALANCE/READ_POSITION/READ_ACTIVITY classification
    (see LiveReadonlyAccountProvider), so the live provider path leaves
    this value honestly as False — it does not block the connection itself.
    Only FakeReadonlyAccountProvider (simulation) sets True."""


@dataclass(frozen=True)
class ConnectionConsent:
    """74 §1 `connection_consent` — a pointer table indicating which Trust
    Core (FND-01) consent record this connection is activated upon.
    Freshness/revocation judgment of the consent itself is owned by Trust
    (71 §4); this table records only "which consent did this connection
    rely on at that point in time."""

    connection_id: UUID
    consent_ref: UUID
    data_purposes: tuple[str, ...]
    expires_at: datetime | None


@dataclass(frozen=True)
class SnapshotValue:
    """A single numeric value contained in a snapshot — shaped as
    (entity_type, entity_key, value) so that reconciliation (FND-08)'s
    `EntitySnapshot.provider_value` can consume it directly. Only
    entity_type="BALANCE" is actually populated (get_positions() returns
    an empty list for all exchanges attached to this leaf, as they are
    all spot — see LiveReadonlyAccountProvider)."""

    entity_type: str
    entity_key: str
    value: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    id: UUID
    connection_id: UUID
    captured_at: datetime
    provider_as_of: datetime
    freshness: str
    currency: str
    source_evidence_ref: str
    values: tuple[SnapshotValue, ...] = field(default_factory=tuple)


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
    """Return value of 74 §3 ReadonlyAccountProvider.verify_readonly_scope() —
    the scope actually granted by the provider (may differ from the request, "scope drift")."""

    granted_scopes: tuple[CapabilityScope, ...]
    provider_credential_ref: str
    provider_verified: bool = False
    """Audit §6 — whether the provider itself independently verified this scope
    (True), or simply assumed the requested scopes as granted (False).
    The exchange API has no lookup tool that directly maps to this taxonomy,
    so LiveReadonlyAccountProvider always returns False — only
    FakeReadonlyAccountProvider (simulation) returns True."""


@dataclass(frozen=True)
class ProviderSnapshot:
    """Return value of 74 §3 ReadonlyAccountProvider.fetch_snapshot()."""

    provider_as_of: datetime
    currency: str
    raw_payload_ref: str = field(default="")
    values: tuple[SnapshotValue, ...] = field(default_factory=tuple)
