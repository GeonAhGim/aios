"""Trust Core domain/rules.py 멤버십 상태 머신 순수함수 단위테스트 — DB 없이 검증.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §3.1.
"""

from src.foundation.trust.domain.models import MembershipRole, MembershipState
from src.foundation.trust.domain.rules import (
    is_membership_transition_allowed,
    role_can,
    would_remove_last_owner,
)

# ----------------------------------------------------------------------
# is_membership_transition_allowed — 73 §3.1 상태 머신 표
# ----------------------------------------------------------------------


def test_admin_can_suspend_active_membership():
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=MembershipRole.ADMIN
        )
        is True
    )


def test_member_cannot_suspend_active_membership():
    """negative — MEMBER는 허용 role 집합(ADMIN/SERVICE) 밖이므로 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=MembershipRole.MEMBER
        )
        is False
    )


def test_owner_can_revoke_active_membership():
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.REVOKED, actor_role=MembershipRole.OWNER
        )
        is True
    )


def test_service_cannot_revoke_active_membership():
    """negative — REVOKE 허용 role 집합은 OWNER/ADMIN뿐, SERVICE는 SUSPEND만
    허용되므로 여기서는 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.REVOKED, actor_role=MembershipRole.SERVICE
        )
        is False
    )


def test_owner_can_reactivate_revoked_membership():
    assert (
        is_membership_transition_allowed(
            MembershipState.REVOKED, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is True
    )


def test_admin_cannot_reactivate_revoked_membership():
    """negative — REVOKED->ACTIVE는 OWNER 전용(재초대 수준의 신뢰 요구)이다.
    ADMIN조차 허용 집합 밖이므로 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.REVOKED, MembershipState.ACTIVE, actor_role=MembershipRole.ADMIN
        )
        is False
    )


def test_same_state_transition_is_always_denied():
    """negative — 표에 없는 전이(자기 자신으로의 전이)는 모든 role에 대해
    거부되어야 한다(§3.1 "표에 없는 전이는 전부 거부")."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


def test_suspended_to_active_direct_path_is_not_in_table():
    """negative — 표에는 SUSPENDED->ACTIVE 직접 경로가 없다(재활성화는
    REVOKED->ACTIVE 경로만 정의됨). OWNER라도 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.SUSPENDED, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


# ----------------------------------------------------------------------
# would_remove_last_owner — 73 §3.1 "마지막 owner 제거 금지" 가드
# ----------------------------------------------------------------------


def test_non_owner_target_never_blocks_transition():
    assert (
        would_remove_last_owner(active_owners=1, target_is_owner=False, to=MembershipState.REVOKED)
        is False
    )


def test_reactivating_owner_never_blocks_even_with_zero_active_owners():
    """to=ACTIVE(재활성화)는 owner 수를 늘리는 방향이므로 절대 막히지 않는다."""
    assert (
        would_remove_last_owner(active_owners=0, target_is_owner=True, to=MembershipState.ACTIVE)
        is False
    )


def test_suspending_the_sole_active_owner_is_blocked():
    assert (
        would_remove_last_owner(active_owners=1, target_is_owner=True, to=MembershipState.SUSPENDED)
        is True
    )


def test_revoking_the_sole_active_owner_is_blocked():
    assert (
        would_remove_last_owner(active_owners=1, target_is_owner=True, to=MembershipState.REVOKED)
        is True
    )


def test_revoking_one_of_two_active_owners_is_allowed():
    """negative(경계) — active_owners=2에서는 다른 owner가 남으므로 허용되어야
    한다. 문턱값 <=1 바로 위 경계를 검증한다."""
    assert (
        would_remove_last_owner(active_owners=2, target_is_owner=True, to=MembershipState.REVOKED)
        is False
    )


# ----------------------------------------------------------------------
# role_can — 73 §8 tenant-confidential 경계(AUDITOR 읽기 전용)
# ----------------------------------------------------------------------


def test_every_role_can_read():
    for role in MembershipRole:
        assert role_can(role, "read") is True


def test_auditor_cannot_mutate():
    """negative — AUDITOR가 mutate 권한을 가지면 §8 "tenant-confidential"
    경계가 무의미해진다(rules.py 자체 docstring 근거)."""
    assert role_can(MembershipRole.AUDITOR, "mutate") is False


def test_auditor_cannot_admin():
    """negative — AUDITOR는 admin 액션도 거부되어야 한다."""
    assert role_can(MembershipRole.AUDITOR, "admin") is False


def test_member_can_mutate_but_not_admin():
    assert role_can(MembershipRole.MEMBER, "mutate") is True
    assert role_can(MembershipRole.MEMBER, "admin") is False


def test_service_can_mutate_but_not_admin():
    assert role_can(MembershipRole.SERVICE, "mutate") is True
    assert role_can(MembershipRole.SERVICE, "admin") is False


def test_owner_and_admin_have_full_admin_rights():
    assert role_can(MembershipRole.OWNER, "admin") is True
    assert role_can(MembershipRole.ADMIN, "admin") is True
