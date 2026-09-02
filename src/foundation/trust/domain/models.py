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
