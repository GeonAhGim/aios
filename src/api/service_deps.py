"""16번대 — 인증/RBAC 외 서비스 팩토리 의존성.

deps.py는 인증 흐름(get_pool/get_current_user/RBAC)에 집중하고, 그 외
서비스별 Depends 팩토리는 여기 모은다 — 섹션이 늘어날수록 deps.py가
무한정 커지지 않도록 분리(feedback_minimal_modules 원칙).
"""
from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from src.core.event_bus.bus import EventBus
from src.core.loader.risk_policy_loader import RiskPolicy, load_risk_policy
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.core.security.key_ring import KeyRing
from src.services.account_deletion_service import AccountDeletionService
from src.services.alert_service import AlertService
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService
from src.services.wallet_service import WalletService
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistService

from .deps import get_event_bus, get_pool


def get_risk_policy() -> RiskPolicy:
    return load_risk_policy()


def get_circuit_breaker_service(
    pool: asyncpg.Pool = Depends(get_pool),
    policy: RiskPolicy = Depends(get_risk_policy),
) -> CircuitBreakerService:
    return CircuitBreakerService(pool, policy.circuit_breaker)


def get_approval_settings_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApprovalSettingsService:
    return ApprovalSettingsService(pool)


def get_withdrawal_whitelist_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    circuit_breaker: CircuitBreakerService = Depends(get_circuit_breaker_service),
    event_bus: EventBus = Depends(get_event_bus),
) -> WithdrawalWhitelistService:
    secrets = request.app.state.secrets
    return WithdrawalWhitelistService(
        pool,
        circuit_breaker,
        encryption_key=secrets.credential_encryption_key.get_secret_value(),
        publish=event_bus.publish,
    )


def get_account_deletion_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> AccountDeletionService:
    return AccountDeletionService(pool)


def get_exchange_credential_service(
    request: Request, pool: asyncpg.Pool = Depends(get_pool)
) -> ExchangeCredentialService:
    secrets = request.app.state.secrets
    key_ring = KeyRing.from_legacy_hex(secrets.credential_encryption_key.get_secret_value())
    return ExchangeCredentialService(pool, key_ring=key_ring)


def get_wallet_service(pool: asyncpg.Pool = Depends(get_pool)) -> WalletService:
    return WalletService(pool)


def get_credential_resolver(request: Request) -> CredentialResolver:
    """레드팀 감사(docs/RED_TEAM_FINDINGS.md #02) 반영 — 매 요청 새로
    만들면 CredentialResolver의 5분 TTL 캐시가 절대 재사용되지 않는다.
    main.py lifespan이 앱 시작 시 한 번만 만들어 둔 것을 그대로 반환한다
    (pool/event_bus와 동일 패턴)."""
    resolver: CredentialResolver = request.app.state.credential_resolver
    return resolver


def get_alert_service(
    pool: asyncpg.Pool = Depends(get_pool),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    event_bus: EventBus = Depends(get_event_bus),
) -> AlertService:
    return AlertService(pool, credential_resolver=resolver, publish=event_bus.publish)
