from src.api.contracts.error_codes import ErrorCode
from src.api.contracts.exception_mapping import ApprovalOwnershipError, map_exception
from src.core.approval.service import ApprovalError
from src.services.auth_service import AuthError
from src.services.credential_resolver import CredentialNotFoundError
from src.services.exchange_credential_service import (
    ExchangeCredentialError,
    ExchangeCredentialNotFoundError,
)
from src.services.mfa_service import MfaError, MfaReauthenticationRequiredError
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


def test_mfa_reauthentication_required_maps_to_auth_mfa_required():
    code, _, _ = map_exception(
        MfaReauthenticationRequiredError("이미 활성화된 MFA를 재설정하려면 재인증이 필요합니다.")
    )
    assert code == ErrorCode.AUTH_MFA_REQUIRED


def test_approval_ownership_error_maps_to_authz_forbidden():
    code, _, _ = map_exception(ApprovalOwnershipError("본인의 승인 요청만 처리할 수 있습니다."))
    assert code == ErrorCode.AUTHZ_FORBIDDEN


def test_exchange_credential_not_found_error_maps_to_resource_not_found_before_base():
    code, _, _ = map_exception(ExchangeCredentialNotFoundError("활성 상태인 자격증명이 없습니다."))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


def test_exchange_credential_error_maps_to_validation_invalid_field():
    code, _, _ = map_exception(ExchangeCredentialError("지원하지 않는 거래소입니다."))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD


def test_credential_not_found_error_maps_to_resource_not_found():
    code, _, _ = map_exception(CredentialNotFoundError("자격증명이 없거나 해지되었습니다."))
    assert code == ErrorCode.RESOURCE_NOT_FOUND


def test_unmapped_exception_falls_back_to_internal_error():
    code, message, _ = map_exception(RuntimeError("전혀 예상 못한 상황"))

    assert code == ErrorCode.INTERNAL_ERROR
    assert message == "전혀 예상 못한 상황"


def test_message_and_no_leaked_internals_for_mapped_exception():
    _, message, _ = map_exception(AuthError("이메일 또는 비밀번호가 올바르지 않습니다."))
    assert message == "이메일 또는 비밀번호가 올바르지 않습니다."


def test_script_compile_error_maps_to_validation_invalid_field_with_details():
    """DSL-12(task-1535) — §3.3 SCRIPT_* 4종은 최상위 코드를 늘리지 않고
    details.code/line/col로 구분한다."""
    from src.core.script.artifact.compile import ScriptCompileError

    code, message, details = map_exception(ScriptCompileError("SCRIPT_TYPE", "미정의 식별자", 3, 7))
    assert code == ErrorCode.VALIDATION_INVALID_FIELD
    assert details == {"code": "SCRIPT_TYPE", "line": 3, "col": 7}
    assert "SCRIPT_TYPE" in message


def test_mapped_exception_without_details_attribute_still_returns_empty_details():
    _, _, details = map_exception(UserAdminError("x"))
    assert details == {}


def test_unmapped_exception_details_attribute_is_not_mapped_code():
    """매핑 표 밖 예외는 INTERNAL_ERROR — 핸들러가 details를 비우므로 새지 않는다."""

    class Leaky(Exception):
        details = {"secret": "x"}

    code, _, _ = map_exception(Leaky("boom"))
    assert code == ErrorCode.INTERNAL_ERROR
