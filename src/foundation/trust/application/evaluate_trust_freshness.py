"""EvaluateTrustFreshness 쿼리.

Spec: AIOSproject 73번 §4 (`EvaluateTrustFreshness`, query — no event).
Mandate/Package/Paper Control 등 다른 FND 컨텍스트가 "이 tenant가 이 purpose에
대해 유효한 동의를 갖고 있는가"만 확인할 때 쓰는 진입점(71번 §4 Contract
ownership — trust가 owner, 다른 context는 consumer).
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
    # get_active_consent가 아니라 get_latest_consent를 쓴다 — "동의한 적 없음"과
    # "동의했다가 철회함"을 구분해야 POLICY_CONSENT_REQUIRED/REVOKED 중 올바른
    # reason_code를 반환할 수 있다(72번 §4 에러 taxonomy).
    consent: Consent | None = await repo.get_latest_consent(context.tenant_id, purpose)

    if disclosure is None:
        # 요구되는 disclosure 자체가 아직 발행되지 않았다 — 정책 미비를
        # "동의 없음"과 구분해 운영자가 알 수 있게 한다.
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
