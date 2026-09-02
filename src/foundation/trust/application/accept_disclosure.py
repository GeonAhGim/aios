"""AcceptDisclosure 커맨드.

Spec: AIOSproject 73번 §4 (`AcceptDisclosure` -> `trust.consent_accepted.v1`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.foundation.trust.contracts.v1 import ConsentDecision, ConsentState, TenantContext
from src.foundation.trust.domain.rules import is_disclosure_acceptable
from src.foundation.trust.ports.repository import TrustRepository

# 73번 §3.2 "ACTIVE becomes unusable after expires_at" — 실제 유효기간은
# 스펙 문서 어디에도 구체 수치가 없다(컴플라이언스 정책 값, 이 리프의 스콥
# 밖). 전수감사(agent-platform-12) 발견 — 이전에는 이 자리에 항상 None을
# 넣어 만료 검사(domain/rules.py의 is_consent_fresh, 이미 올바르게 구현·
# 테스트됨)가 실제로는 한 번도 발동하지 못했다. 명확히 이름 붙인 기본값을
# 하나 두어 그 로직이 실제로 살아있게 한다 — 금융 소비자 동의 재확인
# 주기로 흔히 쓰이는 12개월을 기본값으로 삼되, 실제 컴플라이언스 검토가
# 이 상수 하나만 바꾸면 되도록 분리해둔다.
DEFAULT_CONSENT_VALIDITY = timedelta(days=365)


class DisclosureRetiredError(Exception):
    """73번 §4 VALIDATION_DISCLOSURE_RETIRED — 폐기된 disclosure에는 동의할 수 없다."""


class DisclosureNotFoundError(Exception):
    pass


class ConsentAlreadyActiveError(Exception):
    """73번 §4 STATE_DUPLICATE_COMMAND — 이미 같은 purpose/revision에 ACTIVE 동의가
    있다(uq_consent_record_active_purpose 부분 unique index 위반을 여기서
    도메인 예외로 번역)."""


async def accept_disclosure(
    repo: TrustRepository,
    context: TenantContext,
    *,
    purpose: str,
    disclosure_revision: int,
) -> ConsentDecision:
    disclosure = await repo.get_disclosure_by_purpose_and_revision(purpose, disclosure_revision)
    if disclosure is None:
        raise DisclosureNotFoundError(f"{purpose} revision={disclosure_revision}")

    now = datetime.now(timezone.utc)
    if not is_disclosure_acceptable(disclosure, now=now):
        raise DisclosureRetiredError(f"{purpose} revision={disclosure_revision}은(는) 폐기됨")

    existing = await repo.get_active_consent(context.tenant_id, purpose)
    if existing is not None:
        if existing.disclosure_revision == disclosure_revision:
            raise ConsentAlreadyActiveError(f"{purpose} revision={disclosure_revision}")
        # 73번 §3.2 — 새 revision은 새 ACTIVE 레코드를 요구하고 이전 레코드를
        # 덮어쓰지 않는다(append-only). 다만 uq_consent_record_active_purpose가
        # (tenant_id, purpose)당 ACTIVE 1개만 허용하므로, 이전 revision의
        # 레코드를 먼저 REVOKED로 전이시킨다 — revoke_consent()가 조건부
        # UPDATE(state=ACTIVE)를 쓰므로, 이 사이 다른 요청이 먼저 처리했다면
        # ConcurrencyConflictError로 안전하게 실패한다(105번 §2).
        await repo.revoke_consent(existing.id, tenant_id=context.tenant_id)

    consent = await repo.insert_consent(
        tenant_id=context.tenant_id,
        subject_id=context.subject_id,
        purpose=purpose,
        disclosure_id=disclosure.id,
        disclosure_revision=disclosure_revision,
        expires_at=now + DEFAULT_CONSENT_VALIDITY,
    )
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
