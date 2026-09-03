"""Trust Core 도메인 모델 — pure value object. FastAPI/asyncpg에 의존하지 않는다.

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
    """불변 발행 텍스트/버전. 본문은 별도 document store에 있고 여기는
    `content_hash`만 참조한다(73번 §2.1)."""

    id: UUID
    purpose: str
    revision: int
    content_hash: str
    published_at: datetime
    retired_at: datetime | None


@dataclass(frozen=True)
class Consent:
    """subject의 특정 disclosure purpose/revision에 대한 동의 또는 철회.

    상태 전이(73번 §3.2): NONE -> ACTIVE -> REVOKED. 새 disclosure revision은
    새 ACTIVE 레코드를 요구하며 이전 레코드를 덮어쓰지 않는다 — append-only.
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
    """격리 경계(73번 §2). PERSONAL tenant는 `id == user_id`(84b7d0faf14f 이후
    불변조건, PLT-26 backfill이 이를 만족시킨다)."""

    id: UUID
    kind: TenantKind
    state: TenantState
    created_at: datetime


@dataclass(frozen=True)
class Membership:
    """subject의 tenant 내 역할 바인딩(73번 §3.1 상태 머신). 전이는
    `rules.is_membership_transition_allowed`가 판정한다 — 여기는 값만 담는다."""

    id: UUID
    tenant_id: UUID
    subject_id: UUID
    role: MembershipRole
    state: MembershipState
    revision: int
    created_at: datetime
