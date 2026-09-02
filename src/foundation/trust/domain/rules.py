"""Trust Core 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §6.
"""
from __future__ import annotations

from datetime import datetime

from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure


def is_disclosure_acceptable(disclosure: Disclosure, *, now: datetime) -> bool:
    """폐기된(retired) disclosure는 새 동의를 받을 수 없다(73번 §4
    VALIDATION_DISCLOSURE_RETIRED)."""
    return disclosure.retired_at is None or disclosure.retired_at > now


def is_consent_fresh(
    consent: Consent | None,
    *,
    required_disclosure: Disclosure,
    now: datetime,
) -> bool:
    """73번 §6 규칙 3 — required consent는 purpose와 active disclosure revision을
    모두 만족해야 fresh다. 이전 revision은(재동의 필요 정책이 있다면) 충분하지
    않다. 만료된 동의는 백그라운드 만료 처리가 늦어도 즉시 무효다(73번 §3.2)."""
    if consent is None:
        return False
    if consent.state != ConsentState.ACTIVE:
        return False
    if consent.purpose != required_disclosure.purpose:
        return False
    if consent.disclosure_revision != required_disclosure.revision:
        return False
    if consent.expires_at is not None and now >= consent.expires_at:
        return False
    return True


def freshness_denial_reason(
    consent: Consent | None,
    *,
    required_disclosure: Disclosure,
    now: datetime,
) -> str | None:
    """is_consent_fresh()가 False일 때 72번 §4 에러 taxonomy에 맞는 reason_code를
    돌려준다. fresh하면 None."""
    if consent is None:
        return "POLICY_CONSENT_REQUIRED"
    if consent.state == ConsentState.REVOKED:
        return "POLICY_CONSENT_REVOKED"
    if consent.disclosure_revision != required_disclosure.revision:
        return "POLICY_CONSENT_STALE_REVISION"
    if consent.expires_at is not None and now >= consent.expires_at:
        return "POLICY_CONSENT_EXPIRED"
    return None
