"""L4 §2.3(C) — 도메인 예외 → ErrorCode 매핑 데이터, foundation/* 버킷.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2.3, §3.3. `exception_mapping.py`가
아키텍처 가드 P6.line_cap(300줄)에 닿아 task-1218에서 분리했다 — 이관 순서·설계 근거는 그 모듈
docstring 참조. services/auth 매핑은 `exception_registry.py`(같은 이유로 분리된 자매 모듈)에
있다. 공개 API(`map_exception()`/`override_status()`)는 `exception_mapping.py`가 이 파일과
자매 모듈의 EXCEPTION_MAP_*/STATUS_OVERRIDE_*를 합쳐서 제공한다.
"""
from __future__ import annotations

from starlette import status

from src.api.contracts.error_codes import ErrorCode
from src.api.schemas.positions import InvalidCursorError
from src.foundation.backtest.application.run_backtest import BacktestRunError
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
from src.foundation.positions.application.queries import (
    NavRangeInvalidError,
    PositionAccountNotFoundError,
    PositionNotFoundError,
)
from src.foundation.reconciliation.application.resolve_reconciliation import (
    CrossTenantReconciliationAccessError,
    NotResolvableError,
    ReconciliationStateNotFoundError,
)
from src.foundation.risk_gate.application.activate_rule_bundle import (
    MissingApprovalRefError,
    RuleBundleNotFoundError,
    SelfApprovalError,
    UnauthorizedRuleBundleActorError,
)
from src.foundation.risk_gate.application.activate_safety_control import (
    MissingScopeRefError,
    UnauthorizedSafetyControlScopeError,
)
from src.foundation.risk_gate.application.deactivate_safety_control import (
    SafetyControlNotFoundError,
)
from src.foundation.risk_gate.application.evaluate_risk_gate import (
    CrossTenantConnectionReferenceError,
)
from src.foundation.trust.application.accept_disclosure import (
    ConsentAlreadyActiveError,
    DisclosureNotFoundError,
    DisclosureRetiredError,
)
from src.foundation.trust.application.grant_membership import (
    GrantAuthorizationError,
    MembershipMfaRequiredError,
)
from src.foundation.trust.application.revoke_consent import CrossTenantConsentAccessError
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
from src.foundation.validation.application.start_validation import (
    StrategyNotEligibleForValidationError,
    ValidationAlreadyInProgressError,
)


class UnsupportedStatementScopeError(Exception):
    """PLT-21b(task-1217) — performance.py `scope=LIVE` 거부(LIVE 미배선) — 400 매핑."""


class ConsentNotFoundError(Exception):
    """PLT-21b(task-1218) — trust.py가 revoke_consent.py의 raw LookupError를
    번역(application 파일 수정 불가, builtin 전역 매핑은 회피) — 404 매핑."""


EXCEPTION_MAP_FOUNDATION: list[tuple[type[Exception], ErrorCode]] = [
    # 원장(ledger) — PLT-20 시절부터 있던 매핑, foundation.ledger.*라 여기 둔다.
    (UnknownPayoutBatchError, ErrorCode.RESOURCE_NOT_FOUND),
    (WalletLedgerDriftError, ErrorCode.INTEGRITY_WALLET_BALANCE_DRIFT),
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
    # PLT-21b(task-1218) — foundation/risk_gate·trust·validation.
    (CrossTenantConnectionReferenceError, ErrorCode.RESOURCE_NOT_FOUND),
    (UnauthorizedSafetyControlScopeError, ErrorCode.AUTHZ_FORBIDDEN),
    (MissingScopeRefError, ErrorCode.VALIDATION_INVALID_FIELD),
    (SafetyControlNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (DisclosureNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (DisclosureRetiredError, ErrorCode.VALIDATION_DISCLOSURE_RETIRED),
    (ConsentAlreadyActiveError, ErrorCode.STATE_INVALID_TRANSITION),
    (CrossTenantConsentAccessError, ErrorCode.AUTHZ_FORBIDDEN),
    (ConsentNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (StrategyNotEligibleForValidationError, ErrorCode.STATE_INVALID_TRANSITION),
    (ValidationAlreadyInProgressError, ErrorCode.STATE_INVALID_TRANSITION),
    (BacktestRunError, ErrorCode.VALIDATION_INVALID_FIELD),
    # LB-19(task-1377) — positions 읽기 API(queries.py). 타 테넌트·미존재 동형 404.
    (PositionNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (PositionAccountNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (NavRangeInvalidError, ErrorCode.VALIDATION_INVALID_FIELD),
    (InvalidCursorError, ErrorCode.VALIDATION_INVALID_FIELD),
    # R-23 — foundation/risk_gate rule-bundles(activate_rule_bundle.py).
    (UnauthorizedRuleBundleActorError, ErrorCode.AUTHZ_FORBIDDEN),
    (RuleBundleNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (SelfApprovalError, ErrorCode.AUTHZ_FORBIDDEN),
    (MissingApprovalRefError, ErrorCode.VALIDATION_INVALID_FIELD),
]

STATUS_OVERRIDE_FOUNDATION: list[tuple[type[Exception], int]] = [
    (MethodologyNotFoundError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (DisclosureRetiredError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (BacktestRunError, status.HTTP_422_UNPROCESSABLE_ENTITY),
]
