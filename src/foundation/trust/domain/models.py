"""Trust Core domain model — pure value object. No dependency on FastAPI/asyncpg.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §2.1,
106_module_scaffold_and_naming_standard_v1.0.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class ConsentState(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class TenantKind(str, Enum):
    PERSONAL = "PERSONAL"
    HOUSEHOLD = "HOUSEHOLD"
    ORGANIZATION = "ORGANIZATION"


class TenantState(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class MembershipRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    AUDITOR = "AUDITOR"
    SERVICE = "SERVICE"


class MembershipState(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class Disclosure:
    """Immutable published text/version. The body lives in a separate document store;
    this record holds only `content_hash` (spec 73 §2.1)."""

    id: UUID
    purpose: str
    revision: int
    content_hash: str
    published_at: datetime
    retired_at: datetime | None


@dataclass(frozen=True)
class Consent:
    """Consent or revocation by a subject for a specific disclosure purpose/revision.

    State transitions (spec 73 §3.2): NONE -> ACTIVE -> REVOKED. A new disclosure
    revision requires a new ACTIVE record and never overwrites the prior one — append-only.
    """

    id: UUID
    tenant_id: UUID
    subject_id: UUID
    purpose: str
    disclosure_id: UUID
    disclosure_revision: int
    state: ConsentState
    accepted_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class Tenant:
    """Isolation boundary (spec 73 §2). A PERSONAL tenant has `id == user_id` (invariant
    since commit 84b7d0faf14f; PLT-26 backfill satisfies this)."""

    id: UUID
    kind: TenantKind
    state: TenantState
    created_at: datetime


@dataclass(frozen=True)
class Membership:
    """Role binding of a subject within a tenant (spec 73 §3.1 state machine).
    Transitions are governed by `rules.is_membership_transition_allowed` — this
    class holds only the value."""

    id: UUID
    tenant_id: UUID
    subject_id: UUID
    role: MembershipRole
    state: MembershipState
    revision: int
    created_at: datetime
