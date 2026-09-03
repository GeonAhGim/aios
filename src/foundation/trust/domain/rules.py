"""Trust Core 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §6.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from src.foundation.trust.domain.models import (
    Consent,
    ConsentState,
    Disclosure,
    MembershipRole,
    MembershipState,
)


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


_TransitionKey = tuple[MembershipState, MembershipState]
_MEMBERSHIP_TRANSITIONS: dict[_TransitionKey, frozenset[MembershipRole]] = {
    # 73번 §3.1 상태 머신. "admin/risk"의 risk(자동 위험관리 트리거)는
    # 사람이 아니므로 SERVICE 역할로 매핑한다. REVOKED->ACTIVE의 MFA 요구는
    # 이 순수 함수 밖(호출부)에서 검증한다 — 여기는 role만 판정한다.
    (MembershipState.ACTIVE, MembershipState.SUSPENDED): frozenset(
        {MembershipRole.ADMIN, MembershipRole.SERVICE}
    ),
    (MembershipState.ACTIVE, MembershipState.REVOKED): frozenset(
        {MembershipRole.OWNER, MembershipRole.ADMIN}
    ),
    (MembershipState.SUSPENDED, MembershipState.REVOKED): frozenset(
        {MembershipRole.OWNER, MembershipRole.ADMIN}
    ),
    (MembershipState.REVOKED, MembershipState.ACTIVE): frozenset({MembershipRole.OWNER}),
}


def is_membership_transition_allowed(
    from_: MembershipState, to: MembershipState, *, actor_role: MembershipRole
) -> bool:
    """73번 §3.1 전이표. 표에 없는 전이(같은 상태로의 전이 포함)는 전부 거부다."""
    allowed_actors = _MEMBERSHIP_TRANSITIONS.get((from_, to))
    if allowed_actors is None:
        return False
    return actor_role in allowed_actors


def would_remove_last_owner(active_owners: int, target_is_owner: bool, to: MembershipState) -> bool:
    """73번 §3.1 "cannot remove last owner" 가드. `active_owners`는 대상 membership을
    포함한, 전이 전 시점의 활성 OWNER 수(같은 트랜잭션의 `SELECT ... FOR UPDATE` 결과)."""
    if not target_is_owner:
        return False
    if to == MembershipState.ACTIVE:
        return False
    return active_owners <= 1


def role_can(role: MembershipRole, action: Literal["read", "mutate", "admin"]) -> bool:
    """tenant 내 role의 기본 권한. AUDITOR는 이름 그대로 읽기 전용 — 감사 목적의
    role에 쓰기 권한을 주면 73번 §8 "tenant-confidential" 경계가 무의미해진다."""
    if action == "read":
        return True
    if action == "mutate":
        return role in {
            MembershipRole.OWNER,
            MembershipRole.ADMIN,
            MembershipRole.MEMBER,
            MembershipRole.SERVICE,
        }
    if action == "admin":
        return role in {MembershipRole.OWNER, MembershipRole.ADMIN}
    return False
