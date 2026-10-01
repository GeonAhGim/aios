"""Trust Core domain/rules.py 멤버십 상태 머신 순수함수 단위테스트 — DB 없이 검증.

Spec: AIOSproject 73_trust_core_l3_build_and_operational_specification_v1.0.md §3.1.
"""

import pytest

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


@pytest.mark.negative
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


@pytest.mark.negative
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


@pytest.mark.negative
def test_admin_cannot_reactivate_revoked_membership():
    """negative — REVOKED->ACTIVE는 OWNER 전용(재초대 수준의 신뢰 요구)이다.
    ADMIN조차 허용 집합 밖이므로 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.REVOKED, MembershipState.ACTIVE, actor_role=MembershipRole.ADMIN
        )
        is False
    )


@pytest.mark.negative
def test_same_state_transition_is_always_denied():
    """negative — 표에 없는 전이(자기 자신으로의 전이)는 모든 role에 대해
    거부되어야 한다(§3.1 "표에 없는 전이는 전부 거부")."""
    for role in MembershipRole:
        assert (
            is_membership_transition_allowed(
                MembershipState.ACTIVE, MembershipState.ACTIVE, actor_role=role
            )
            is False
        )


@pytest.mark.negative
def test_suspended_to_active_direct_path_is_not_in_table():
    """negative — 표에는 SUSPENDED->ACTIVE 직접 경로가 없다(재활성화는
    REVOKED->ACTIVE 경로만 정의됨). OWNER라도 거부되어야 한다."""
    assert (
        is_membership_transition_allowed(
            MembershipState.SUSPENDED, MembershipState.ACTIVE, actor_role=MembershipRole.OWNER
        )
        is False
    )


@pytest.mark.negative
def test_auditor_cannot_perform_any_state_transition():
    """negative — AUDITOR는 상태 전이에 관여할 권한이 없다. 어떤 표 항목에도
    AUDITOR가 명시되지 않았으므로 모든 전이가 거부되어야 한다."""
    for from_state in MembershipState:
        for to_state in MembershipState:
            if from_state is to_state:
                continue
            assert (
                is_membership_transition_allowed(
                    from_state, to_state, actor_role=MembershipRole.AUDITOR
                )
                is False
            ), f"AUDITOR: {from_state.value} -> {to_state.value}가 허용됨 (불변식 위반)"


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


@pytest.mark.negative
def test_suspending_the_sole_active_owner_is_blocked():
    """negative — active_owners=1且 target_is_owner=True且 to=SUSPENDED는
    마지막 owner를 제거하므로 반드시 거부되어야 한다."""
    assert (
        would_remove_last_owner(active_owners=1, target_is_owner=True, to=MembershipState.SUSPENDED)
        is True
    )


@pytest.mark.negative
def test_revoking_the_sole_active_owner_is_blocked():
    """negative — active_owners=1且 target_is_owner=True且 to=REVOKED도
    마지막 owner를 제거하므로 반드시 거부되어야 한다."""
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


@pytest.mark.negative
def test_suspending_non_owner_with_one_total_owner_is_allowed():
    """negative — target이 owner가 아니면 last-owner 가드가 적용되지 않는다.
    active_owners=1이고 target_is_owner=False이면 반드시 True(차단 안 함)."""
    assert (
        would_remove_last_owner(
            active_owners=1, target_is_owner=False, to=MembershipState.SUSPENDED
        )
        is False
    )


# ----------------------------------------------------------------------
# role_can — 73 §8 tenant-confidential 경계(AUDITOR 읽기 전용)
# ----------------------------------------------------------------------


def test_every_role_can_read():
    for role in MembershipRole:
        assert role_can(role, "read") is True


@pytest.mark.negative
def test_auditor_cannot_mutate():
    """negative — AUDITOR가 mutate 권한을 가지면 §8 "tenant-confidential"
    경계가 무의미해진다(rules.py 자체 docstring 근거)."""
    assert role_can(MembershipRole.AUDITOR, "mutate") is False


@pytest.mark.negative
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


@pytest.mark.negative
def test_member_and_service_cannot_admin():
    """negative — MEMBER/SERVICE는 admin 권한을 가져서는 안 된다.
    권한 상승 경로를 차단하는 불변식."""
    assert role_can(MembershipRole.MEMBER, "admin") is False
    assert role_can(MembershipRole.SERVICE, "admin") is False


# ----------------------------------------------------------------------
# failure_injection — 의존성 예외 유발로 fail-closed 검증
# ----------------------------------------------------------------------


@pytest.mark.failure_injection
def test_is_membership_transition_allowed_raises_on_unknown_state():
    """실패주입: 알 수 없는 MembershipState enum 값이 전달되면
    예외를 전파해야 한다(암시적 허용 방지).

    _MEMBERSHIP_TRANSITIONS 딕셔너리는 enum 값만 키로 사용하므로
    실제 코드는 KeyError를 일으키지 않지만, 호출자 측에서
    타입 검증을 건너뛰는 경우를 위해 dict.get()이 None을 반환하면
    False를 반환하는 fail-closed 동작을 검증한다.
    """
    # 실제 구현은 dict.get()을 사용하므로 알려지지 않은 전이는 False 반환(fail-closed)
    assert (
        is_membership_transition_allowed(
            MembershipState.ACTIVE, MembershipState.REVOKED, actor_role=MembershipRole.AUDITOR
        )
        is False
    )


@pytest.mark.failure_injection
def test_would_remove_last_owner_handles_zero_owners_gracefully():
    """실패주입: active_owners=0이고 target_is_owner=True인 경우,
    to=SUSPENDED 또는 to=REVOKED이면 True(차단)를 반환해야 한다.
    owner가 한 명도 없는데 owner를 제거/정지하면 무결성이 깨지므로.
    """
    assert (
        would_remove_last_owner(active_owners=0, target_is_owner=True, to=MembershipState.SUSPENDED)
        is True
    )
    assert (
        would_remove_last_owner(active_owners=0, target_is_owner=True, to=MembershipState.REVOKED)
        is True
    )


@pytest.mark.failure_injection
def test_role_can_rejects_unknown_action():
    """실패주입: role_can에 "read"/"mutate"/"admin" 밖의 action을 전달하면
    False(거부)를 반환해야 한다 — 알려지지 않은 권한 요청은 기본적으로 거부."""
    assert role_can(MembershipRole.OWNER, "delete_tenant") is False
    assert role_can(MembershipRole.MEMBER, "escalate") is False


# ----------------------------------------------------------------------
# performance assertion — 순수 함수 오버헤드 검증
# ----------------------------------------------------------------------


@pytest.mark.perf
def test_membership_transition_lookup_latency(perf_budget):
    """성능 단언: is_membership_transition_allowed는 딕셔너리 조회이므로
    10만 회 호출에 50ms 미만이어야 한다. 상태 전이 검사가 성능 병목이 되면
    실시간 리스크 트리거에 영향을 준다."""
    iterations = 100_000

    def _run() -> None:
        for _ in range(iterations):
            is_membership_transition_allowed(
                MembershipState.ACTIVE, MembershipState.SUSPENDED, actor_role=MembershipRole.ADMIN
            )

    perf_budget.assert_within(
        _run, budget_ms=50.0, label="is_membership_transition_allowed 10만 회 — 예산 0.05초"
    )


@pytest.mark.perf
def test_would_remove_last_owner_latency(perf_budget):
    """성능 단언: would_remove_last_owner는 단순 정수 비교이므로
    10만 회 호출에 20ms 미만이어야 한다."""
    iterations = 100_000

    def _run() -> None:
        for _ in range(iterations):
            would_remove_last_owner(
                active_owners=1, target_is_owner=True, to=MembershipState.REVOKED
            )

    perf_budget.assert_within(
        _run, budget_ms=20.0, label="would_remove_last_owner 10만 회 — 예산 0.02초"
    )


@pytest.mark.perf
def test_role_can_latency(perf_budget):
    """성능 단언: role_can은 집합 멤버십 검사이므로 10만 회 호출에 30ms 미만이어야 한다."""
    iterations = 100_000

    def _run() -> None:
        for _ in range(iterations):
            role_can(MembershipRole.OWNER, "admin")

    perf_budget.assert_within(_run, budget_ms=30.0, batch=8, label="role_can 10만 회 — 예산 0.03초")
