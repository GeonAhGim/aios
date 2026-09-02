"""foundation/* 라우터 공용 의존성 — src/api/suitability_deps.py와 동일 패턴."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


# 73번 §6 규칙 2 "issued within configured step-up window" — 이 창을 넘으면
# mfa_enabled=True인 계정이라도 "최근 실제로 TOTP를 통과했다"고 볼 수 없다.
# 로그인 세션(JWT, 기본 60분)보다 짧게 잡는다 — 세션이 살아있는 동안에도
# 민감 커맨드는 "로그인했다"가 아니라 "최근 재확인했다"를 요구해야 한다.
MFA_STEP_UP_WINDOW = timedelta(minutes=15)


def get_tenant_context(user: User = Depends(get_current_user)) -> TenantContext:
    """71번 §4 "API body에서 생성 금지" — 게이트웨이 인증(get_current_user)에서만
    발급한다. P0 스콥은 tenant_id == subject_id == user_id다(84b7d0faf14f 마이그레이션
    편차 설명 참조 — organization/household tenant는 아직 없음).

    전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 반영 —
    예전에는 `mfa_verified = user.mfa_enabled`로, "계정에 MFA가 켜져 있다"(계정
    설정)와 "이 세션이 최근 실제로 TOTP를 통과했다"(세션 사실)를 구분하지
    못했다. `auth_service.py`의 `mfa_verified_at`(TOTP 통과 시각, 마이그레이션
    cdd905e63ffe)을 기준으로 `MFA_STEP_UP_WINDOW` 안에 있을 때만 True다 —
    로그인 후 오래 켜둔 세션은 다시 step-up하지 않는 한 "MFA 검증됨"으로
    보지 않는다. mfa_enabled=False인 계정은 여전히 항상 False(애초에 검증할
    대상이 없다)."""
    mfa_verified = (
        user.mfa_enabled
        and user.mfa_verified_at is not None
        and (datetime.now(timezone.utc) - user.mfa_verified_at) <= MFA_STEP_UP_WINDOW
    )
    return TenantContext(
        tenant_id=user.user_id,
        subject_id=user.user_id,
        role="OWNER",
        mfa_verified=mfa_verified,
    )
