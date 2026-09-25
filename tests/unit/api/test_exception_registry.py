"""QA task-1163 회귀 — 관리자 3경로(UserAdmin/Verification/WalletTopup)의
존재하지 않는 대상(404)·상태 전이 위반(409)이 400 VALIDATION_INVALID_FIELD로
뭉개지지 않는지 검증한다.

task-4147 DEEPEN — negative(부모 코드로의 오매핑 거부, 미등록 예외의 fail-closed
기본값)·실패주입(등록 순서 손상 시 서브클래스 코드가 부모 코드로 붕괴함을 재현,
= 이 파일이 막으려는 원 버그의 gate-red 재현)·성능단언(순수 인메모리 조회이므로
호출당 예산을 명시적으로 건다)을 보강한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.3
"""

import time

import pytest

from src.api.contracts import exception_mapping as exception_mapping_module
from src.api.contracts.error_codes import ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.api.contracts.exception_registry import EXCEPTION_MAP_SERVICES
from src.services.user_admin_service import UserAdminError, UserAdminNotFoundError
from src.services.verification_service import (
    VerificationError,
    VerificationInvalidTransitionError,
    VerificationNotFoundError,
)
from src.services.wallet_service import (
    WalletTopupError,
    WalletTopupInvalidTransitionError,
    WalletTopupNotFoundError,
)


class _UnrelatedDependencyError(Exception):
    """등록 표(EXCEPTION_MAP)에 없는 임의 예외 — fail-closed 기본 경로 검증용."""


def test_unrelated_exception_defaults_to_internal_error_not_an_admin_code():
    """negative — EXCEPTION_MAP에 없는 예외는 관리자 3경로의 어떤 코드로도
    새지 않고 fail-closed 기본값(INTERNAL_ERROR)으로 떨어져야 한다."""
    code, _, details = map_exception(_UnrelatedDependencyError("의존성 계약 밖의 예외"))
    assert code == ErrorCode.INTERNAL_ERROR
    assert details == {}


def test_user_admin_base_error_is_not_resource_not_found():
    """negative — 부모(UserAdminError)는 자식(UserAdminNotFoundError)의
    404 코드를 절대 받으면 안 된다(등록 순서가 맞아도 타입 자체가 다르므로)."""
    code, _, _ = map_exception(UserAdminError("운영자는 ACTIVE/SUSPENDED로만..."))
    assert code != ErrorCode.RESOURCE_NOT_FOUND


def test_verification_base_error_is_not_state_invalid_transition():
    """negative — VerificationError(부모)가 VerificationInvalidTransitionError
    (자식)의 409 코드로 오매핑되면 안 된다."""
    code, _, _ = map_exception(VerificationError("알 수 없는 결정입니다: MAYBE"))
    assert code != ErrorCode.STATE_INVALID_TRANSITION


def test_wallet_topup_not_found_is_not_state_invalid_transition():
    """negative — 같은 서비스 안에서도 404(NotFound)와 409(InvalidTransition)
    사유가 서로 섞이면 안 된다(둘 다 부모보다 앞에 등록되지만 서로 구분돼야 함)."""
    code, _, _ = map_exception(WalletTopupNotFoundError("존재하지 않는 충전 요청입니다."))
    assert code != ErrorCode.STATE_INVALID_TRANSITION


def test_corrupted_registration_order_collapses_notfound_into_parent_code(monkeypatch):
    """실패주입/gate-red 재현 — EXCEPTION_MAP 등록 순서가 손상되어 부모
    (UserAdminError)가 자식(UserAdminNotFoundError)보다 먼저 오면, 이 파일이
    막으려던 원래 버그(404가 400으로 뭉개짐)가 실제로 재현됨을 monkeypatch로
    증명한다 — `test_subclasses_are_registered_before_their_parent`가 순서
    선언만 고정한다면, 이 테스트는 그 선언이 깨졌을 때 실제 map_exception()
    반환값이 어떻게 망가지는지까지 검증한다."""
    corrupted_order = [
        (UserAdminError, ErrorCode.VALIDATION_INVALID_FIELD),
        (UserAdminNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    ]
    monkeypatch.setattr(exception_mapping_module, "EXCEPTION_MAP", corrupted_order)

    code, _, _ = exception_mapping_module.map_exception(
        UserAdminNotFoundError("존재하지 않는 사용자입니다.")
    )

    assert code == ErrorCode.VALIDATION_INVALID_FIELD


@pytest.mark.perf
def test_map_exception_lookup_stays_within_budget():
    """성능단언(D2) — map_exception은 순수 인메모리 리스트 스캔(I/O 없음)이라
    호출 5,000회가 500ms 안에 끝나야 한다(로컬 CI 기준 여유 있는 예산; 이
    범위를 넘으면 EXCEPTION_MAP에 O(n^2) 회귀가 들어왔다는 신호)."""
    exc = WalletTopupNotFoundError("존재하지 않는 충전 요청입니다.")

    start = time.perf_counter()
    for _ in range(5000):
        map_exception(exc)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5


def test_user_admin_not_found_maps_to_resource_not_found_before_base():
    code, _, _ = map_exception(UserAdminNotFoundError("존재하지 않는 사용자입니다."))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


def test_user_admin_base_error_still_maps_to_validation_invalid_field():
    code, _, _ = map_exception(UserAdminError("운영자는 ACTIVE/SUSPENDED로만..."))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD


def test_verification_not_found_maps_to_resource_not_found_before_base():
    code, _, _ = map_exception(VerificationNotFoundError("존재하지 않는 리스팅입니다."))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


def test_verification_invalid_transition_maps_to_state_invalid_transition_before_base():
    code, _, _ = map_exception(
        VerificationInvalidTransitionError("PENDING_VERIFICATION 상태에서만...")
    )
    assert code == ErrorCode.STATE_INVALID_TRANSITION


def test_verification_base_error_still_maps_to_validation_invalid_field():
    code, _, _ = map_exception(VerificationError("알 수 없는 결정입니다: MAYBE"))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD


def test_wallet_topup_not_found_maps_to_resource_not_found_before_base():
    code, _, _ = map_exception(WalletTopupNotFoundError("존재하지 않는 충전 요청입니다."))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


def test_wallet_topup_invalid_transition_maps_to_state_invalid_transition_before_base():
    code, _, _ = map_exception(
        WalletTopupInvalidTransitionError("이미 다른 관리자가 처리했습니다(동시 처리 충돌).")
    )
    assert code == ErrorCode.STATE_INVALID_TRANSITION


def test_wallet_topup_base_error_still_maps_to_validation_invalid_field():
    code, _, _ = map_exception(WalletTopupError("충전 금액은 0보다 커야 합니다."))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD


def _index_of(exc_type: type[Exception]) -> int:
    for i, (t, _) in enumerate(EXCEPTION_MAP_SERVICES):
        if t is exc_type:
            return i
    raise AssertionError(f"{exc_type}가 EXCEPTION_MAP_SERVICES에 없습니다.")


def test_subclasses_are_registered_before_their_parent():
    """매핑 우선순위 고정 — 이 순서가 뒤바뀌면(부모가 먼저 오면) 위 개별
    테스트들이 실제로는 통과해도(현재 파이썬 등록 상태에서만) 회귀를 못
    잡는다. 등록 순서 자체를 명시적으로 고정해 향후 재배치 실수를 막는다."""
    assert _index_of(UserAdminNotFoundError) < _index_of(UserAdminError)
    assert _index_of(VerificationNotFoundError) < _index_of(VerificationError)
    assert _index_of(VerificationInvalidTransitionError) < _index_of(VerificationError)
    assert _index_of(WalletTopupNotFoundError) < _index_of(WalletTopupError)
    assert _index_of(WalletTopupInvalidTransitionError) < _index_of(WalletTopupError)
