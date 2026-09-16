"""PLT-26 tenant_membership 핵심 순수함수 단위테스트 — DB 없이 상태 전이표/
마지막 OWNER 가드/역할 권한만 검증한다."""

from __future__ import annotations

import pytest

from src.foundation.trust.domain.models import MembershipRole, MembershipState
from src.foundation.trust.domain.rules import (
    is_membership_transition_allowed,
    role_can,
    would_remove_last_owner,
)


@pytest.mark.parametrize(
    "from_,to,actor_role",
    [
        (MembershipState.ACTIVE, MembershipState.SUSPENDED, MembershipRole.ADMIN),
        (MembershipState.ACTIVE, MembershipState.SUSPENDED, MembershipRole.SERVICE),
        (MembershipState.ACTIVE, MembershipState.REVOKED, MembershipRole.OWNER),
        (MembershipState.ACTIVE, MembershipState.REVOKED, MembershipRole.ADMIN),
        (MembershipState.SUSPENDED, MembershipState.REVOKED, MembershipRole.OWNER),
        (MembershipState.REVOKED, MembershipState.ACTIVE, MembershipRole.OWNER),
    ],
)
def test_allowed_transitions_by_authorized_role(from_, to, actor_role):
    assert is_membership_transition_allowed(from_, to, actor_role=actor_role) is True


def test_member_role_cannot_suspend():
    """73번 §3.1 전이표에 MEMBER는 어떤 상태전이의 actor로도 등장하지 않는다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=MembershipRole.MEMBER
        )
        is False
    )


def test_admin_cannot_regrant_from_revoked():
    """REVOKED->ACTIVE(regrant)는 OWNER 전용 — ADMIN은 거부돼야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.REVOKED, MembershipState.ACTIVE, actor_role=MembershipRole.ADMIN
        )
        is False
    )


def test_same_state_transition_is_always_denied():
    """표에 없는 전이(동일 상태로의 전이 포함)는 actor role과 무관하게 거부한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


def test_suspended_to_active_is_not_a_defined_transition():
    """SUSPENDED->ACTIVE는 73번 §3.1 전이표에 없다 — grant_membership.py는 이 경로를
    쓰지 않는다(별도 unsuspend 리프가 없는 한)."""
    assert (
        is_membership_transition_allowed(
            MembershipState.SUSPENDED, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


def test_last_owner_cannot_be_suspended():
    assert would_remove_last_owner(1, True, MembershipState.SUSPENDED) is True


def test_last_owner_cannot_be_revoked():
    assert would_remove_last_owner(1, True, MembershipState.REVOKED) is True


def test_non_owner_never_blocked_regardless_of_owner_count():
    assert would_remove_last_owner(1, False, MembershipState.REVOKED) is False


def test_owner_transition_to_active_never_blocked():
    """to=ACTIVE(regrant)는 제거가 아니므로 active_owners가 0이어도 막지 않는다."""
    assert would_remove_last_owner(0, True, MembershipState.ACTIVE) is False


def test_second_owner_can_be_suspended():
    assert would_remove_last_owner(2, True, MembershipState.SUSPENDED) is False


@pytest.mark.parametrize("role", list(MembershipRole))
def test_every_role_can_read(role):
    assert role_can(role, "read") is True


def test_auditor_cannot_mutate():
    """AUDITOR는 감사 목적 read-only — mutate 권한을 주면 73번 §8
    tenant-confidential 경계가 무의미해진다."""
    assert role_can(MembershipRole.AUDITOR, "mutate") is False


def test_auditor_cannot_admin():
    assert role_can(MembershipRole.AUDITOR, "admin") is False


@pytest.mark.parametrize(
    "role",
    [MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.MEMBER, MembershipRole.SERVICE],
)
def test_operational_roles_can_mutate(role):
    assert role_can(role, "mutate") is True


@pytest.mark.parametrize(
    "role", [MembershipRole.MEMBER, MembershipRole.SERVICE, MembershipRole.AUDITOR]
)
def test_non_admin_roles_cannot_admin(role):
    assert role_can(role, "admin") is False
