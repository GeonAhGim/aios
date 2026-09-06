"""QA task-1163 회귀 — 관리자 3경로(UserAdmin/Verification/WalletTopup)의
존재하지 않는 대상(404)·상태 전이 위반(409)이 400 VALIDATION_INVALID_FIELD로
뭉개지지 않는지 검증한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.3
"""
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
