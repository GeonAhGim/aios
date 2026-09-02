"""RevokeConsent 커맨드.

Spec: AIOSproject 73번 §4 (`RevokeConsent` -> `trust.consent_revoked.v1`).
"""
from __future__ import annotations

from uuid import UUID

from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState, TenantContext
from src.foundation.trust.ports.repository import TrustRepository


class CrossTenantConsentAccessError(Exception):
    """73번 §4 AUTH_TENANT_MISMATCH — 다른 tenant의 동의는 조회/철회할 수 없다."""


async def revoke_consent(
    repo: TrustRepository, context: TenantContext, *, consent_id: UUID
) -> ConsentDecision:
    try:
        consent = await repo.revoke_consent(consent_id, tenant_id=context.tenant_id)
    except PermissionError as exc:
        raise CrossTenantConsentAccessError(str(exc)) from exc

    return ConsentDecision(
        consent_id=consent.id,
        tenant_id=consent.tenant_id,
        purpose=consent.purpose,
        disclosure_id=consent.disclosure_id,
        disclosure_revision=consent.disclosure_revision,
        state=ConsentState(consent.state.value),
        accepted_at=consent.accepted_at,
        revoked_at=consent.revoked_at,
        expires_at=consent.expires_at,
    )
