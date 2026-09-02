from src.api.contracts.error_codes import ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.core.approval.service import ApprovalError
from src.services.auth_service import AuthError
from src.services.mfa_service import MfaError
from src.services.user_admin_service import UserAdminError


def test_auth_error_maps_to_generic_invalid_credentials():
    """레드팀 #12 방어 유지 — 실패 사유별로 다른 코드를 주면 HTTP
    상태코드 자체가 새 계정열거 채널이 된다. AuthError는 항상 이
    코드 하나로만 매핑돼야 한다."""
    code, message, details = map_exception(AuthError("이메일 또는 비밀번호가 올바르지 않습니다."))

    assert code == ErrorCode.AUTH_INVALID_CREDENTIALS


def test_mfa_error_maps_to_auth_mfa_invalid():
    code, _, _ = map_exception(MfaError("인증 코드가 올바르지 않습니다."))
    assert code == ErrorCode.AUTH_MFA_INVALID


def test_approval_error_maps_to_state_invalid_transition():
    code, _, _ = map_exception(ApprovalError("이미 처리된 요청"))
    assert code == ErrorCode.STATE_INVALID_TRANSITION


def test_user_admin_error_maps_to_validation_invalid_field():
    code, _, _ = map_exception(UserAdminError("운영자는 ACTIVE/SUSPENDED로만..."))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD


def test_unmapped_exception_falls_back_to_internal_error():
    code, message, _ = map_exception(RuntimeError("전혀 예상 못한 상황"))

    assert code == ErrorCode.INTERNAL_ERROR
    assert message == "전혀 예상 못한 상황"


def test_message_and_no_leaked_internals_for_mapped_exception():
    _, message, _ = map_exception(AuthError("이메일 또는 비밀번호가 올바르지 않습니다."))
    assert message == "이메일 또는 비밀번호가 올바르지 않습니다."
