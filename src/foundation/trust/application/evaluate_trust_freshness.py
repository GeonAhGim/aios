"""EvaluateTrustFreshness query.

Spec: AIOSproject #73 §4 (`EvaluateTrustFreshness`, query — no event).
Entry point used by other FND contexts (Mandate/Package/Paper Control, etc.) when they
need to check "does this tenant hold valid consent for this purpose"
(#71 §4 Contract ownership — trust is the owner, other contexts are consumers).
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.foundation.trust.contracts.v1 import TenantContext, TrustFreshnessDecision
from src.foundation.trust.domain.models import Consent, Disclosure
from src.foundation.trust.domain.rules import freshness_denial_reason, is_consent_fresh
from src.foundation.trust.ports.repository import TrustRepository


async def evaluate_trust_freshness(
    repo: TrustRepository, context: TenantContext, *, purpose: str
) -> TrustFreshnessDecision:
    now = datetime.now(timezone.utc)
    disclosure: Disclosure | None = await repo.get_active_disclosure(purpose)
    # Use get_latest_consent, not get_active_consent — we must distinguish
    # "never consented" from "consented then revoked" to return the correct
    # reason_code (POLICY_CONSENT_REQUIRED vs REVOKED) per #72 §4 error taxonomy.
    consent: Consent | None = await repo.get_latest_consent(context.tenant_id, purpose)

    if disclosure is None:
        # The required disclosure has not yet been published — let the operator
        # distinguish this from "no consent" (e.g. POLICY_DISCLOSURE_NOT_PUBLISHED
        # vs POLICY_CONSENT_REQUIRED).
        return TrustFreshnessDecision(
            tenant_id=context.tenant_id,
            purpose=purpose,
            is_fresh=False,
            reason_code="POLICY_DISCLOSURE_NOT_PUBLISHED",
            as_of=now,
        )

    fresh = is_consent_fresh(consent, required_disclosure=disclosure, now=now)
    reason = None if fresh else freshness_denial_reason(
        consent, required_disclosure=disclosure, now=now
    )
    return TrustFreshnessDecision(
        tenant_id=context.tenant_id,
        purpose=purpose,
        is_fresh=fresh,
        reason_code=reason,
        as_of=now,
    )
