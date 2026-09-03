"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

PLT-108(auth/users/admin) 이후 PLT-17(auth/users/exchange_credentials)이
exchange_credentials·credential_resolver 예외를 추가했다 — 다른
라우터(marketplace/foundation/executions 등)가 쓰는 예외는 각 라우터를
이관하는 후속 리프(PLT-18~21)에서 추가한다.

한계(정직하게 명시): 이 코드베이스의 여러 예외 클래스(UserAdminError/
SellerSuspensionError/WalletTopupError/ListingError 등)는 서비스 하나당
예외 클래스 하나로 묶여 있어, "존재하지 않음"/"검증 실패"/"상태 충돌"
등 서로 다른 사유가 같은 타입 안에 섞여 있다. `EXCEPTION_MAP`은 타입
기반 매핑이라 이런 클래스는 가장 대표적인 사유 하나로만 매핑된다 —
완벽한 사유별 구분이 필요하면 해당 서비스의 예외 계층을 먼저 세분화
해야 한다(이번 리프 스콥 밖).

AuthError는 의도적으로 AUTH_INVALID_CREDENTIALS 하나로만 매핑한다 —
AuthService.authenticate()가 계정 미존재/잠금/정지/틀린 비밀번호를
전부 같은 예외+같은 메시지로 뭉뚱그리는 것 자체가 레드팀 감사 #12가
막은 계정열거 타이밍 사이드채널의 핵심 방어다. 여기서 실패 종류별로
다른 ErrorCode(예: AUTH_ACCOUNT_LOCKED)를 반환하면 HTTP 상태코드
자체가 새로운 열거 채널이 되어 그 방어를 무력화한다.
"""
from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.core.approval.service import ApprovalError
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.ledger.application.payouts import UnknownPayoutBatchError
from src.foundation.ledger.application.queries import WalletLedgerDriftError
from src.services.account_deletion_service import AccountDeletionError
from src.services.approval_settings_service import ApprovalSettingsError
from src.services.auth_service import AuthError
from src.services.credential_resolver import CredentialNotFoundError
from src.services.dispute_resolution_service import DisputeResolutionError
from src.services.exchange_credential_service import (
    ExchangeCredentialError,
    ExchangeCredentialNotFoundError,
)
from src.services.listing_service import ListingError
from src.services.mfa_service import MfaError, MfaReauthenticationRequiredError
from src.services.seller_suspension_service import SellerSuspensionError
from src.services.user_admin_service import UserAdminError
from src.services.wallet_service import WalletTopupError
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistError


class ApprovalOwnershipError(Exception):
    """타인 소유 승인 요청을 처리하려는 시도(src/api/routers/users.py
    `_require_own_request`) — AUTHZ_FORBIDDEN(403). core/approval/service.py
    가 아니라 여기 두는 이유는 승인 워크플로 자체의 불변식이 아니라
    라우터 계층의 소유권 검사이기 때문이다(P6 300줄 상한도 고려)."""


# 선언 순서 = 우선순위(서브클래스 먼저) — 지금은 전부 최상위 Exception의
# 직접 서브클래스라 순서가 결과에 영향을 주지 않지만, 나중에 서브클래싱이
# 생기면 이 순서 규칙을 지켜야 한다.
EXCEPTION_MAP: list[tuple[type[Exception], ErrorCode]] = [
    (AuthError, ErrorCode.AUTH_INVALID_CREDENTIALS),
    (MfaReauthenticationRequiredError, ErrorCode.AUTH_MFA_REQUIRED),
    (MfaError, ErrorCode.AUTH_MFA_INVALID),
    (ApprovalSettingsError, ErrorCode.VALIDATION_INVALID_FIELD),
    (WithdrawalWhitelistError, ErrorCode.POLICY_DENIED),
    (AccountDeletionError, ErrorCode.STATE_INVALID_TRANSITION),
    (ApprovalOwnershipError, ErrorCode.AUTHZ_FORBIDDEN),
    (ApprovalError, ErrorCode.STATE_INVALID_TRANSITION),
    (UserAdminError, ErrorCode.VALIDATION_INVALID_FIELD),
    (DisputeResolutionError, ErrorCode.STATE_INVALID_TRANSITION),
    (SellerSuspensionError, ErrorCode.RESOURCE_NOT_FOUND),
    (WalletTopupError, ErrorCode.VALIDATION_INVALID_FIELD),
    (ListingError, ErrorCode.VALIDATION_INVALID_FIELD),
    (UnknownPayoutBatchError, ErrorCode.RESOURCE_NOT_FOUND),
    (WalletLedgerDriftError, ErrorCode.INTEGRITY_WALLET_BALANCE_DRIFT),
    (ConcurrencyConflictError, ErrorCode.STATE_CONCURRENCY_CONFLICT),
    (ExchangeCredentialNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (ExchangeCredentialError, ErrorCode.VALIDATION_INVALID_FIELD),
    (CredentialNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
]


def map_exception(exc: Exception) -> tuple[ErrorCode, str, dict[str, object]]:
    """(ErrorCode, message, details) — details는 지금은 항상 빈 dict다
    (§3.3 details.fields/reason_codes 채우기는 검증기·정책엔진 쪽에
    구조화된 사유가 생기는 후속 리프에서)."""
    for exc_type, code in EXCEPTION_MAP:
        if isinstance(exc, exc_type):
            return code, str(exc), {}
    return ErrorCode.INTERNAL_ERROR, str(exc), {}
