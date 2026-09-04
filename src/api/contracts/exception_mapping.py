"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

PLT-108(auth/users/admin) → PLT-17(auth/users/exchange_credentials) →
PLT-18(marketplace/strategy_builder/suitability) → PLT-20(notifications/
alerts/device_tokens/wallet) → PLT-19(executions/portfolio/reports) →
PLT-29(trust_memberships) → task-1108(foundation/connections·mandates·
evidence)이 각자 쓰는 예외를 추가했다 — 나머지 foundation 라우터(paper_
control/performance/reconciliation/risk_gate/trust/validation)는 후속
직렬 리프(task-1217·1218)에서 추가한다.

PLT-20 decision(task-1017)은 "src/api/contracts/** 공용 파일 수정 금지"
(PLT-18/19와의 병렬 실행 중 충돌 회피 목적)였지만, alert_service의
AlertError·device_token_service의 DeviceTokenError를 라우터에서 그대로
propagate하되 이 파일을 건드리지 않으면 미매핑 예외가 INTERNAL_ERROR(500)로
떨어져 기존 라우터 테스트(test_alerts_router.py::test_cancel_nonexistent_
alert_returns_404, test_device_tokens_router.py의 400/404 케이스)가 깨진다
— "기존 테스트 무수정 통과"가 "공용 파일 수정 금지"보다 우선한다고 판단해
신규 ErrorCode 없이(§3.3 taxonomy 재사용만) 최소 추가했다. PM 검토 필요시
task-1017 note 참조.

한계(정직하게 명시): 이 코드베이스의 여러 예외 클래스(UserAdminError/
SellerSuspensionError/WalletTopupError/ListingError/PurchaseError 등)는
서비스 하나당 예외 클래스 하나로 묶여 있어, "존재하지 않음"/"검증 실패"/
"상태 충돌" 등 서로 다른 사유가 같은 타입 안에 섞여 있다. `EXCEPTION_MAP`은
타입 기반 매핑이라 이런 클래스는 가장 대표적인 사유 하나로만 매핑된다 —
완벽한 사유별 구분이 필요하면 해당 서비스의 예외 계층을 먼저 세분화
해야 한다(이번 리프 스콥 밖). `StrategyNotFoundError`/`RiskProfileNotFoundError`/
`AlertNotFoundError`/`DeviceTokenNotFoundError`처럼 실제로 라우터가 상태코드를
구분해야 했던 경우만 서브클래스로 쪼갰다(PLT-17의
`ExchangeCredentialNotFoundError`와 동일 근거).

AuthError는 의도적으로 AUTH_INVALID_CREDENTIALS 하나로만 매핑한다 —
AuthService.authenticate()가 계정 미존재/잠금/정지/틀린 비밀번호를
전부 같은 예외+같은 메시지로 뭉뚱그리는 것 자체가 레드팀 감사 #12가
막은 계정열거 타이밍 사이드채널의 핵심 방어다. 여기서 실패 종류별로
다른 ErrorCode(예: AUTH_ACCOUNT_LOCKED)를 반환하면 HTTP 상태코드
자체가 새로운 열거 채널이 되어 그 방어를 무력화한다.

`STATUS_OVERRIDE`/`override_status()`: `HTTP_STATUS`(error_codes.py)는
ErrorCode 하나당 상태코드 하나만 고정한다. 그런데 마켓플레이스 구매의
잔액부족(402)·AI 프롬프트 생성 미구현(501)은 기존 라우터 테스트가
그 정확한 상태코드를 그대로 기대하면서도, 402/501에 대응하는 별도
ErrorCode를 새로 만들 근거는 없다(taxonomy 접두 화이트리스트에 PAYMENT_/
NOT_IMPLEMENTED_ 같은 새 접두를 추가하는 것은 이번 리프 스콥 밖). 그래서
handlers.py의 HTTPException 분기가 이미 하던 것(봉투의 error_code는
매핑값을 쓰되 HTTP status만 개별 override)과 동일한 원리를 도메인 예외
쪽에도 그대로 적용한다 — `map_exception()`의 3-tuple 반환 시그니처는
바꾸지 않는다(기존 test_exception_mapping.py 무수정 유지).
"""
from __future__ import annotations

from starlette import status

from src.api.contracts.error_codes import ErrorCode
from src.core.approval.service import ApprovalError
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.indicators.talib_adapter import IndicatorError
from src.foundation.connections.application.begin_connection import (
    ConsentRequiredError,
    MfaRequiredError,
)
from src.foundation.connections.application.confirm_connection import (
    ScopeVerificationFailedError,
)
from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.application.revoke_connection import ConnectionNotRevocableError
from src.foundation.connections.application.sync_snapshot import (
    ConnectionNotSyncableError,
    ConnectionRevokedDuringSyncError,
    ProviderUnavailableError,
)
from src.foundation.connections.domain.rules import ForbiddenCapabilityScopeError
from src.foundation.evidence.domain.rules import ChainIntegrityError
from src.foundation.ledger.application.payouts import UnknownPayoutBatchError
from src.foundation.ledger.application.queries import WalletLedgerDriftError
from src.foundation.mandates.application.activate_revision import (
    CoolingOffNotElapsedError,
    CrossTenantMandateAccessError,
    InvalidRevisionStateError,
    MaterialChangeRequiresFreshConsentError,
    MaterialChangeRequiresReauthError,
    RevisionNotFoundError,
)
from src.foundation.mandates.application.create_draft_mandate import MandateAlreadyExistsError
from src.foundation.mandates.application.evaluate_policy import NoActiveMandateError
from src.foundation.trust.application.grant_membership import (
    GrantAuthorizationError,
    MembershipMfaRequiredError,
)
from src.foundation.trust.application.revoke_membership import (
    RevokeAuthorizationError,
    RevokeLastOwnerError,
    RevokeTargetNotFoundError,
)
from src.foundation.trust.application.suspend_membership import (
    SuspendAuthorizationError,
    SuspendLastOwnerError,
    SuspendTargetNotFoundError,
)
from src.services.account_deletion_service import AccountDeletionError
from src.services.alert_service import AlertError, AlertNotFoundError
from src.services.approval_settings_service import ApprovalSettingsError
from src.services.auth.logout import LogoutSessionMismatchError
from src.services.auth.refresh import RefreshSessionNotFoundError, RefreshTokenExpiredError
from src.services.auth.session_repository import RefreshReuseDetected
from src.services.auth.tokens import TokenExpiredError, TokenInvalidError
from src.services.auth_service import AccountLockedError, AuthError
from src.services.capital_allocation import CapitalAllocationError
from src.services.condition_compiler import ConditionCompileError
from src.services.credential_resolver import CredentialNotFoundError
from src.services.device_token_service import DeviceTokenError, DeviceTokenNotFoundError
from src.services.dispute_resolution_service import DisputeResolutionError
from src.services.dispute_service import DisputeError
from src.services.exchange_credential_service import (
    ExchangeCredentialError,
    ExchangeCredentialNotFoundError,
)
from src.services.execution_service import ExecutionControlError, ExecutionCreateError
from src.services.listing_service import ListingError
from src.services.mfa_service import MfaError, MfaReauthenticationRequiredError
from src.services.portfolio_service import RebalanceError
from src.services.purchase_service import InsufficientWalletBalanceError, PurchaseError
from src.services.review_service import ReviewError
from src.services.risk_profile_service import RiskProfileError, RiskProfileNotFoundError
from src.services.seller_suspension_service import SellerSuspensionError
from src.services.strategy_access_service import StrategyAccessError
from src.services.strategy_builder_service import StrategyLifecycleError, StrategyNotFoundError
from src.services.strategy_prompt_service import PromptGenerationUnavailableError
from src.services.strategy_wizard_service import WizardError
from src.services.user_admin_service import UserAdminError
from src.services.verification_service import VerificationError
from src.services.wallet_service import WalletTopupError
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistError


class ApprovalOwnershipError(Exception):
    """타인 소유 승인 요청을 처리하려는 시도(src/api/routers/users.py
    `_require_own_request`) — AUTHZ_FORBIDDEN(403). core/approval/service.py
    가 아니라 여기 두는 이유는 승인 워크플로 자체의 불변식이 아니라
    라우터 계층의 소유권 검사이기 때문이다(P6 300줄 상한도 고려)."""


class SessionRevokedError(Exception):
    """PLT-24 — `src/api/deps.py`의 `get_current_user()`가 access JWT의
    서명·claims는 유효하지만 그 `sid`의 `auth_session`이 이미 revoke된
    경우 던진다(로그아웃·재사용 탐지·admin suspend 등). `ApprovalOwnershipError`
    와 동일한 이유로 API 계층 소유 예외를 여기 둔다 — 401
    `AUTH_SESSION_REVOKED`로 매핑."""


# 선언 순서 = 우선순위(서브클래스 먼저) — 지금은 전부 최상위 Exception의
# 직접 서브클래스라 순서가 결과에 영향을 주지 않지만, 나중에 서브클래싱이
# 생기면 이 순서 규칙을 지켜야 한다.
EXCEPTION_MAP: list[tuple[type[Exception], ErrorCode]] = [
    # PLT-24 — AccountLockedError는 AuthError 서브클래스라 반드시 그
    # 앞에 온다(선언 순서 = 우선순위). TokenExpiredError도 TokenInvalidError
    # 서브클래스라 마찬가지다(tokens.py).
    (AccountLockedError, ErrorCode.AUTH_ACCOUNT_LOCKED),
    (AuthError, ErrorCode.AUTH_INVALID_CREDENTIALS),
    (TokenExpiredError, ErrorCode.AUTH_TOKEN_EXPIRED),
    (TokenInvalidError, ErrorCode.AUTH_TOKEN_INVALID),
    (RefreshReuseDetected, ErrorCode.AUTH_SESSION_REVOKED),
    (RefreshSessionNotFoundError, ErrorCode.AUTH_SESSION_REVOKED),
    (RefreshTokenExpiredError, ErrorCode.AUTH_TOKEN_EXPIRED),
    (LogoutSessionMismatchError, ErrorCode.AUTHZ_FORBIDDEN),
    (SessionRevokedError, ErrorCode.AUTH_SESSION_REVOKED),
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
    # PLT-18 — marketplace/strategy_builder/suitability.
    (InsufficientWalletBalanceError, ErrorCode.POLICY_DENIED),
    (PurchaseError, ErrorCode.VALIDATION_INVALID_FIELD),
    (VerificationError, ErrorCode.VALIDATION_INVALID_FIELD),
    (StrategyAccessError, ErrorCode.AUTHZ_FORBIDDEN),
    (ReviewError, ErrorCode.VALIDATION_INVALID_FIELD),
    (DisputeError, ErrorCode.VALIDATION_INVALID_FIELD),
    (IndicatorError, ErrorCode.VALIDATION_INVALID_FIELD),
    (ConditionCompileError, ErrorCode.VALIDATION_INVALID_FIELD),
    (StrategyNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (StrategyLifecycleError, ErrorCode.VALIDATION_INVALID_FIELD),
    (WizardError, ErrorCode.VALIDATION_INVALID_FIELD),
    (PromptGenerationUnavailableError, ErrorCode.DEPENDENCY_NOT_READY),
    (RiskProfileNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (RiskProfileError, ErrorCode.VALIDATION_INVALID_FIELD),
    # PLT-20 — notifications/alerts/device_tokens/wallet.
    (AlertNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (AlertError, ErrorCode.VALIDATION_INVALID_FIELD),
    (DeviceTokenNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (DeviceTokenError, ErrorCode.VALIDATION_INVALID_FIELD),
    # PLT-19 — executions/portfolio/reports.
    (ExecutionCreateError, ErrorCode.VALIDATION_INVALID_FIELD),
    (ExecutionControlError, ErrorCode.VALIDATION_INVALID_FIELD),
    (CapitalAllocationError, ErrorCode.VALIDATION_INVALID_FIELD),
    (RebalanceError, ErrorCode.VALIDATION_INVALID_FIELD),
    # PLT-29 — trust_memberships(grant/suspend/revoke). 새 ErrorCode를 만들지
    # 않고 기존 taxonomy만 재사용한다(decision, task-1103). 73번 §4.1
    # STATE_DUPLICATE_COMMAND/STATE_LAST_OWNER는 존재하지 않아 각각
    # ConcurrencyConflictError(이미 등록됨)/STATE_INVALID_TRANSITION으로 접는다.
    (MembershipMfaRequiredError, ErrorCode.AUTH_MFA_REQUIRED),
    (GrantAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (SuspendTargetNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (SuspendAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (SuspendLastOwnerError, ErrorCode.STATE_INVALID_TRANSITION),
    (RevokeTargetNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (RevokeAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (RevokeLastOwnerError, ErrorCode.STATE_INVALID_TRANSITION),
    # PLT-21(task-1108) — foundation/connections·mandates·evidence. 기존
    # taxonomy만 재사용한다(decision, 새 ErrorCode 신설 금지) — 각 항목의
    # 상태코드는 이관 전 라우터가 쓰던 값과 그대로 일치해 STATUS_OVERRIDE가
    # 필요 없다.
    (MfaRequiredError, ErrorCode.AUTH_MFA_REQUIRED),
    (ConsentRequiredError, ErrorCode.POLICY_DENIED),
    (ForbiddenCapabilityScopeError, ErrorCode.VALIDATION_INVALID_FIELD),
    (ConnectionNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantConnectionAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (ScopeVerificationFailedError, ErrorCode.EXCHANGE_FATAL),
    (ConnectionNotSyncableError, ErrorCode.STATE_INVALID_TRANSITION),
    (ConnectionRevokedDuringSyncError, ErrorCode.STATE_INVALID_TRANSITION),
    (ProviderUnavailableError, ErrorCode.EXCHANGE_FATAL),
    (ConnectionNotRevocableError, ErrorCode.STATE_INVALID_TRANSITION),
    (MandateAlreadyExistsError, ErrorCode.STATE_INVALID_TRANSITION),
    (NoActiveMandateError, ErrorCode.RESOURCE_NOT_FOUND),
    (RevisionNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantMandateAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (InvalidRevisionStateError, ErrorCode.STATE_INVALID_TRANSITION),
    (MaterialChangeRequiresReauthError, ErrorCode.AUTH_MFA_REQUIRED),
    (MaterialChangeRequiresFreshConsentError, ErrorCode.POLICY_DENIED),
    (CoolingOffNotElapsedError, ErrorCode.STATE_INVALID_TRANSITION),
    # evidence.py 체인 무결성 위반 — 79번 §4가 원래 쓰던
    # `INTEGRITY_AUDIT_CHAIN_BROKEN`은 taxonomy에 없어(신규 코드 신설 금지)
    # 기존 409 conflict 버킷(STATE_INVALID_TRANSITION)에 접는다 — PLT-20의
    # AccountDeletionError/ApprovalError와 동일 근거(상태코드는 그대로 409).
    (ChainIntegrityError, ErrorCode.STATE_INVALID_TRANSITION),
]

# ErrorCode 하나당 상태코드 하나뿐인 HTTP_STATUS로 표현할 수 없는 개별
# 상태코드(모듈 docstring 참조) — 없으면 HTTP_STATUS[code]를 그대로 쓴다.
STATUS_OVERRIDE: list[tuple[type[Exception], int]] = [
    (InsufficientWalletBalanceError, status.HTTP_402_PAYMENT_REQUIRED),
    (PromptGenerationUnavailableError, status.HTTP_501_NOT_IMPLEMENTED),
]


def map_exception(exc: Exception) -> tuple[ErrorCode, str, dict[str, object]]:
    """(ErrorCode, message, details) — details는 지금은 항상 빈 dict다
    (§3.3 details.fields/reason_codes 채우기는 검증기·정책엔진 쪽에
    구조화된 사유가 생기는 후속 리프에서)."""
    for exc_type, code in EXCEPTION_MAP:
        if isinstance(exc, exc_type):
            return code, str(exc), {}
    return ErrorCode.INTERNAL_ERROR, str(exc), {}


def override_status(exc: Exception) -> int | None:
    """`STATUS_OVERRIDE`에 등록된 예외면 그 상태코드를, 아니면 None을
    반환한다 — None이면 호출자가 `HTTP_STATUS[code]` 기본값을 쓴다."""
    for exc_type, status_code in STATUS_OVERRIDE:
        if isinstance(exc, exc_type):
            return status_code
    return None
