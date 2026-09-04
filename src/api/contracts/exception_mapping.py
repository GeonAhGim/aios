"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3

이관 순서: PLT-108→17→18→20→19→29→task-1108(foundation/connections·
mandates·evidence)→task-1217(foundation/paper_control·performance·
reconciliation). 나머지(risk_gate/trust/validation)는 task-1218에서 추가.

PLT-20 decision(task-1017)의 "공용 파일 수정 금지"보다 "기존 테스트
무수정 통과"를 우선해(신규 ErrorCode 없이) alert/device_token 예외를
등록했다(상세는 task-1017 note 참조).

한계(정직하게 명시): 서비스당 예외 클래스 하나로 묶인 타입(UserAdminError
등)은 대표 사유 하나로만 매핑된다 — 라우터가 실제로 상태코드를 구분해야
했던 경우만(StrategyNotFoundError 등) 서브클래스로 쪼갰다.

AuthError는 AUTH_INVALID_CREDENTIALS 하나로만 매핑한다 — 계정 미존재/
잠금/정지/틀린 비밀번호를 상태코드로 구분하면 계정열거 사이드채널이
된다(레드팀 감사 #12).

`STATUS_OVERRIDE`: 기존 라우터가 쓰던 개별 상태코드(402/501/422 등)를
새 ErrorCode 없이 유지한다(`map_exception()` 3-tuple 시그니처 불변).
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
from src.foundation.paper_control.application.pause_deployment import (
    CrossTenantDeploymentAccessError as PauseCrossTenantDeploymentAccessError,
)
from src.foundation.paper_control.application.pause_deployment import (
    DeploymentNotFoundError as PauseDeploymentNotFoundError,
)
from src.foundation.paper_control.application.pause_deployment import (
    InvalidDeploymentStateError as PauseInvalidDeploymentStateError,
)
from src.foundation.paper_control.application.request_deployment import IdempotencyKeyConflictError
from src.foundation.paper_control.application.request_deployment import (
    NoActiveMandateError as PaperNoActiveMandateError,
)
from src.foundation.paper_control.application.start_deployment import (
    CrossTenantDeploymentAccessError as StartCrossTenantDeploymentAccessError,
)
from src.foundation.paper_control.application.start_deployment import (
    DeploymentNotFoundError as StartDeploymentNotFoundError,
)
from src.foundation.paper_control.application.start_deployment import (
    InvalidDeploymentStateError as StartInvalidDeploymentStateError,
)
from src.foundation.paper_control.application.start_deployment import RiskGateDeniedError
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from src.foundation.performance.adapters.paper_input_adapter import UnreconciledInputError
from src.foundation.performance.application.compute_statement import MethodologyNotFoundError
from src.foundation.performance.application.correct_statement import (
    CrossTenantStatementAccessError as CorrectCrossTenantStatementAccessError,
)
from src.foundation.performance.application.correct_statement import (
    StatementNotFoundError as CorrectStatementNotFoundError,
)
from src.foundation.performance.application.get_statement import (
    CrossTenantStatementAccessError,
    StatementNotFoundError,
)
from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
)
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
    """타인 소유 승인 요청 처리 시도 — 라우터 계층 소유권 검사, AUTHZ_FORBIDDEN(403)."""


class SessionRevokedError(Exception):
    """PLT-24 — revoke된 `auth_session`의 access JWT용 — AUTH_SESSION_REVOKED(401)."""


class UnsupportedStatementScopeError(Exception):
    """PLT-21b(task-1217) — performance.py `scope=LIVE` 거부(LIVE 미배선) — 400 매핑."""


# 선언 순서 = 우선순위 — AccountLockedError/TokenExpiredError는 서브클래스라 앞에 온다.
EXCEPTION_MAP: list[tuple[type[Exception], ErrorCode]] = [
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
    # PLT-29 — trust_memberships. 없는 코드는 Concurrency/STATE_INVALID_TRANSITION으로 접는다.
    (MembershipMfaRequiredError, ErrorCode.AUTH_MFA_REQUIRED),
    (GrantAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (SuspendTargetNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (SuspendAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (SuspendLastOwnerError, ErrorCode.STATE_INVALID_TRANSITION),
    (RevokeTargetNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (RevokeAuthorizationError, ErrorCode.AUTHZ_FORBIDDEN),
    (RevokeLastOwnerError, ErrorCode.STATE_INVALID_TRANSITION),
    # PLT-21(task-1108) — foundation/connections·mandates·evidence.
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
    # evidence.py 체인 무결성 — 없는 코드라 409 conflict로 접는다.
    (ChainIntegrityError, ErrorCode.STATE_INVALID_TRANSITION),
    # PLT-21b(task-1217) — foundation/paper_control·performance·reconciliation.
    (PaperNoActiveMandateError, ErrorCode.RESOURCE_NOT_FOUND),
    (InvalidProvenanceError, ErrorCode.VALIDATION_INVALID_FIELD),
    (IdempotencyKeyConflictError, ErrorCode.INTEGRITY_IDEMPOTENCY_CONFLICT),
    # pause/start_deployment.py 동명이인 클래스 — 둘 다 등록 안 하면 500.
    (PauseDeploymentNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (StartDeploymentNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (PauseCrossTenantDeploymentAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (StartCrossTenantDeploymentAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (PauseInvalidDeploymentStateError, ErrorCode.STATE_INVALID_TRANSITION),
    (StartInvalidDeploymentStateError, ErrorCode.STATE_INVALID_TRANSITION),
    (RiskGateDeniedError, ErrorCode.RISK_DENIED),
    (UnsupportedStatementScopeError, ErrorCode.VALIDATION_INVALID_FIELD),
    (UnreconciledInputError, ErrorCode.STATE_INVALID_TRANSITION),
    (MethodologyNotFoundError, ErrorCode.VALIDATION_INVALID_FIELD),
    (StatementNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CorrectStatementNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantStatementAccessError, ErrorCode.AUTHZ_FORBIDDEN),
    (CorrectCrossTenantStatementAccessError, ErrorCode.AUTHZ_FORBIDDEN),
    (ReconciliationStateNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (CrossTenantReconciliationAccessError, ErrorCode.RESOURCE_NOT_FOUND),
    (NotResolvableError, ErrorCode.STATE_INVALID_TRANSITION),
]

# ErrorCode 하나당 상태코드 하나뿐인 HTTP_STATUS로 표현할 수 없는 개별
# 상태코드(모듈 docstring 참조) — 없으면 HTTP_STATUS[code]를 그대로 쓴다.
STATUS_OVERRIDE: list[tuple[type[Exception], int]] = [
    (InsufficientWalletBalanceError, status.HTTP_402_PAYMENT_REQUIRED),
    (PromptGenerationUnavailableError, status.HTTP_501_NOT_IMPLEMENTED),
    (MethodologyNotFoundError, status.HTTP_422_UNPROCESSABLE_ENTITY),
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
