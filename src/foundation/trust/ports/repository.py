"""Trust Core repository port. domain은 이 Protocol만 알고, 실제 구현(adapters/)은
모른다 — 71번 §4 "domain은 FastAPI·SQLAlchemy·외부 HTTP에 의존하지 않는다"."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.foundation.trust.domain.models import Consent, Disclosure


class TrustRepository(Protocol):
    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        """해당 purpose의 현재 활성(미폐기) disclosure 중 최신 revision을 반환."""
        ...

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> Disclosure | None: ...

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        """해당 tenant/purpose의 현재 ACTIVE 동의(있다면 정확히 하나)."""
        ...

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        """상태(ACTIVE/REVOKED) 무관하게 가장 최근 레코드 — freshness 판정이
        "동의한 적 없음"(POLICY_CONSENT_REQUIRED)과 "동의했다가 철회함"
        (POLICY_CONSENT_REVOKED)을 구분하려면 REVOKED 레코드도 봐야 한다.
        `get_active_consent`는 ACTIVE만 보므로 이 구분을 할 수 없다."""
        ...

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        """TrustStatusView 프로젝션(projections.py)이 쓰는 조회 — 해당 tenant의
        현재 ACTIVE 동의 전체."""
        ...

    async def insert_consent(
        self,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        purpose: str,
        disclosure_id: UUID,
        disclosure_revision: int,
        expires_at: datetime | None,
    ) -> Consent:
        """새 ACTIVE consent를 append한다 — 기존 레코드를 덮어쓰지 않는다(73번
        §3.2 append-only)."""
        ...

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        """105번 표준의 조건부 UPDATE(state=ACTIVE 조건)로 REVOKED 전이.

        대상이 이미 REVOKED이거나 다른 tenant 소유면
        `ConcurrencyConflictError`/`PermissionError`를 던진다(구현체 책임)."""
        ...
