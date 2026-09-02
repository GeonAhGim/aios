"""foundation/* 라우터 공용 의존성 — src/api/suitability_deps.py와 동일 패턴."""
from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from src.api.deps import get_current_user, get_pool
from src.foundation.connections.adapters.fake_provider import FakeReadonlyAccountProvider
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.ports.provider import ReadonlyAccountProvider
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.paper_control.adapters.postgres_repository import (
    PostgresPaperControlRepository,
)
from src.foundation.paper_control.ports.repository import PaperControlRepository
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.ports.repository import ReconciliationRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.ports.repository import TrustRepository
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.ports.repository import ValidationRepository
from src.services.auth_service import User


def get_trust_repository(pool: asyncpg.Pool = Depends(get_pool)) -> TrustRepository:
    return PostgresTrustRepository(pool)


def get_mandate_repository(pool: asyncpg.Pool = Depends(get_pool)) -> MandateRepository:
    return PostgresMandateRepository(pool)


def get_audit_event_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> AuditEventRepository:
    return PostgresAuditEventRepository(pool)


def get_connection_repository(pool: asyncpg.Pool = Depends(get_pool)) -> ConnectionRepository:
    return PostgresConnectionRepository(pool)


def get_validation_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ValidationRepository:
    return PostgresValidationRepository(pool)


def get_risk_gate_repository(pool: asyncpg.Pool = Depends(get_pool)) -> RiskGateRepository:
    return PostgresRiskGateRepository(pool)


def get_paper_control_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> PaperControlRepository:
    return PostgresPaperControlRepository(pool)


def get_reconciliation_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReconciliationRepository:
    return PostgresReconciliationRepository(pool)


# 74번 §7 rollout gate 1단계 — 실 provider는 71번 §6 "provider/legal review 후
# 결정" 대상이라 아직 없다. 이 함수 하나만 바꾸면 실 provider로 교체된다
# (adapters/fake_provider.py 참조).
def get_readonly_account_provider() -> ReadonlyAccountProvider:
    return FakeReadonlyAccountProvider()


def get_credential_encryption_key(request: Request) -> str:
    secrets = request.app.state.secrets
    return str(secrets.credential_encryption_key.get_secret_value())


def get_tenant_context(user: User = Depends(get_current_user)) -> TenantContext:
    """71번 §4 "API body에서 생성 금지" — 게이트웨이 인증(get_current_user)에서만
    발급한다. P0 스콥은 tenant_id == subject_id == user_id다(84b7d0faf14f 마이그레이션
    편차 설명 참조 — organization/household tenant는 아직 없음).

    `mfa_verified`는 계정에 MFA가 켜져 있는지를 그대로 옮긴다 — 로그인 자체가
    mfa_enabled=True인 계정에는 TOTP 통과를 강제하므로(auth_service.py), 유효한
    세션이 있다는 것 자체가 "이 세션은 MFA를 통과했다"는 뜻이다. 민감 커맨드별
    step-up 재인증(73번 §6 규칙 2)은 이 리프의 스콥이 아니다 — 기존
    `reauthenticate()`(deps.py) 패턴을 개별 라우터가 필요할 때 그대로 쓴다."""
    return TenantContext(
        tenant_id=user.user_id,
        subject_id=user.user_id,
        role="OWNER",
        mfa_verified=user.mfa_enabled,
    )
