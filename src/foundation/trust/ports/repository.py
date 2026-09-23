"""Trust Core repository port. domain은 이 Protocol만 알고, 실제 구현(adapters/)은
모른다 — 71번 §4 "domain은 FastAPI·SQLAlchemy·외부 HTTP에 의존하지 않는다"."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.foundation.trust.domain.models import Consent, Disclosure


class TrustRepository(Protocol):
    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        """Return the latest revision of the currently active
        (non-destroyed) disclosure for the given purpose."""
        ...

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> Disclosure | None: ...

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        """Return the currently ACTIVE consent for this tenant/purpose (at most one)."""
        ...

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        """Most recent record regardless of state (ACTIVE/REVOKED). Distinguishing
        "never consented" (POLICY_CONSENT_REQUIRED) from "consented then revoked"
        (POLICY_CONSENT_REVOKED) requires seeing REVOKED records.
        `get_active_consent` only sees ACTIVE, so it cannot make this distinction."""
        ...

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        """Query used by the TrustStatusView projection (projections.py) — all
        currently ACTIVE consents for this tenant."""
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
        """Append a new ACTIVE consent — never overwrite an existing record (§3.2 append-only)."""
        ...

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        """Conditional UPDATE (state=ACTIVE guard) per the standard-105 pattern
        to transition to REVOKED.

        Raises `ConcurrencyConflictError`/`PermissionError` if the target is
        already REVOKED or belongs to another tenant (implementor responsibility)."""
        ...
