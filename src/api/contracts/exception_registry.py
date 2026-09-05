"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑 데이터, services/auth 버킷.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3. `exception_mapping.py`가
아키텍처 가드 P6.line_cap(300줄)에 닿아 task-1218에서 분리했다 — 이관 순서·설계 근거는 그 모듈
docstring 참조. `src.foundation.*` 매핑은 `exception_registry_foundation.py`(같은 이유로 분리된
자매 모듈)에 있다. 공개 API(`map_exception()`/`override_status()`)는 `exception_mapping.py`가
이 파일과 자매 모듈의 EXCEPTION_MAP_*/STATUS_OVERRIDE_*를 합쳐서 제공한다.
"""
from __future__ import annotations

from starlette import status

from src.api.contracts.error_codes import ErrorCode
from src.core.approval.service import ApprovalError
from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.indicators.talib_adapter import IndicatorError
from src.core.script.artifact.compile import ScriptCompileError
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


# 선언 순서 = 우선순위 — AccountLockedError/TokenExpiredError는 서브클래스라 앞에 온다.
EXCEPTION_MAP_SERVICES: list[tuple[type[Exception], ErrorCode]] = [
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
    # DSL-12(task-1535) — §3.3 SCRIPT_* 4종은 새 최상위 코드 없이 400 하나로
    # 매핑하고 구분은 `details.code/line/col`(ScriptCompileError.details)로 한다.
    (ScriptCompileError, ErrorCode.VALIDATION_INVALID_FIELD),
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
    # PLT-29(trust_memberships)·원장(ledger) 매핑은 foundation.* 소스라
    # exception_registry_foundation.py의 EXCEPTION_MAP_FOUNDATION에 있다.
]

STATUS_OVERRIDE_SERVICES: list[tuple[type[Exception], int]] = [
    (InsufficientWalletBalanceError, status.HTTP_402_PAYMENT_REQUIRED),
    (PromptGenerationUnavailableError, status.HTTP_501_NOT_IMPLEMENTED),
]
