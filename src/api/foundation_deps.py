"""foundation/* 라우터 공용 의존성 — src/api/suitability_deps.py와 동일 패턴."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from fastapi import Depends, HTTPException, Request, status

from src.api.contracts.error_codes import ErrorCode
from src.api.deps import get_current_user, get_pool
from src.api.service_deps import get_credential_resolver
from src.core.observability.tenant_binding import rebind_tenant
from src.exchanges.factory import SUPPORTED_EXCHANGES
from src.foundation.connections.adapters.live_provider import LiveReadonlyAccountProvider
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
from src.foundation.performance.adapters.paper_input_adapter import PaperStatementInputAdapter
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.ports.repository import PerformanceRepository, StatementInputPort
from src.foundation.reconciliation.adapters.postgres_repository import (
    PostgresReconciliationRepository,
)
from src.foundation.reconciliation.ports.repository import ReconciliationRepository
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.application.resolve_tenant_context import (
    TenantMismatchError,
    resolve_tenant_context,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.ports.membership_repository import MembershipRepository
from src.foundation.trust.ports.repository import TrustRepository
from src.foundation.validation.adapters.postgres_repository import PostgresValidationRepository
from src.foundation.validation.ports.repository import ValidationRepository
from src.services.auth_service import User
from src.services.credential_resolver import CredentialResolver


def get_trust_repository(pool: asyncpg.Pool = Depends(get_pool)) -> TrustRepository:
    return PostgresTrustRepository(pool)


def get_membership_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> MembershipRepository:
    return PostgresMembershipRepository(pool)


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


def get_performance_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> PerformanceRepository:
    return PostgresPerformanceRepository(pool)


def get_paper_statement_input_adapter(
    pool: asyncpg.Pool = Depends(get_pool),
) -> StatementInputPort:
    return PaperStatementInputAdapter(pool)


# 전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §6) 발견 반영 —
# 예전에는 여기서 무조건 FakeReadonlyAccountProvider를 반환해, 운영 경로가
# 실제로는 한 번도 실 거래소를 만지지 않았다. 이제 connection_id 경로
# 파라미터(FastAPI가 이름으로 자동 바인딩)로 connection을 먼저 읽어
# provider_code에 맞는 실 어댑터(legacy CredentialResolver 기반,
# adapters/live_provider.py)를 구성한다 — Fake는 이제 이 DI 경로 어디서도
# 안 쓴다. 테스트가 필요하면 애플리케이션 함수를 직접 호출하며 Fake를
# 명시적으로 넘기거나(기존 통합테스트가 이미 그렇게 함), FastAPI
# `app.dependency_overrides`로 이 함수 자체를 교체한다 — "테스트 전용
# 플래그로만" 도달 가능하다는 게 이 뜻이다.
async def get_readonly_account_provider(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> ReadonlyAccountProvider:
    connection = await connection_repo.get_connection(connection_id)
    if connection is None or connection.tenant_id != user.user_id:
        # 존재하지 않거나 다른 tenant 소유 — 여기서 곧장 404. 커맨드
        # 함수도 같은 검사를 다시 하지만(방어적 중복, 74번 §5), provider를
        # 만들 수 없는 이 시점에는 어차피 더 진행할 수 없다.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "존재하지 않는 연결입니다.")
    if connection.provider_code not in SUPPORTED_EXCHANGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"지원하지 않는 provider_code={connection.provider_code}입니다.",
        )
    return LiveReadonlyAccountProvider(
        resolver,
        user_id=user.user_id,
        exchange=connection.provider_code,
        requested_capability_profile=connection.capability_profile,
    )


def get_credential_encryption_key(request: Request) -> str:
    secrets = request.app.state.secrets
    return str(secrets.credential_encryption_key.get_secret_value())


# 73번 §6 규칙 2 "issued within configured step-up window" — 이 창을 넘으면
# mfa_enabled=True인 계정이라도 "최근 실제로 TOTP를 통과했다"고 볼 수 없다.
# 로그인 세션(JWT, 기본 60분)보다 짧게 잡는다 — 세션이 살아있는 동안에도
# 민감 커맨드는 "로그인했다"가 아니라 "최근 재확인했다"를 요구해야 한다.
MFA_STEP_UP_WINDOW = timedelta(minutes=15)


def _compute_mfa_verified(user: User) -> bool:
    """전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 반영 —
    예전에는 `mfa_verified = user.mfa_enabled`로, "계정에 MFA가 켜져 있다"(계정
    설정)와 "이 세션이 최근 실제로 TOTP를 통과했다"(세션 사실)를 구분하지
    못했다. `auth_service.py`의 `mfa_verified_at`(TOTP 통과 시각, 마이그레이션
    cdd905e63ffe)을 기준으로 `MFA_STEP_UP_WINDOW` 안에 있을 때만 True다 —
    로그인 후 오래 켜둔 세션은 다시 step-up하지 않는 한 "MFA 검증됨"으로
    보지 않는다. mfa_enabled=False인 계정은 여전히 항상 False(애초에 검증할
    대상이 없다). PLT-28 — `get_tenant_context`가 async I/O(헤더/DB)를 갖게
    되면서, 순수 계산만 하던 기존 단위테스트(`test_foundation_deps.py`)가
    계속 동기적으로 부를 수 있게 이 부분만 별도 함수로 뺐다(시그니처 안정)."""
    return bool(
        user.mfa_enabled
        and user.mfa_verified_at is not None
        and (datetime.now(timezone.utc) - user.mfa_verified_at) <= MFA_STEP_UP_WINDOW
    )


async def get_tenant_context(
    request: Request,
    user: User = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
    membership_repo: MembershipRepository = Depends(get_membership_repository),
) -> TenantContext:
    """71번 §4 "API body에서 생성 금지" — 게이트웨이 인증(get_current_user)에서만
    발급한다. `X-Tenant-Id` 헤더가 없으면 personal tenant(id == user_id)로
    발급하고(P0 스콥, 84b7d0faf14f 마이그레이션 편차 설명 참조), 있으면
    PLT-28 `resolve_tenant_context`가 그 tenant에 대한 활성 멤버십을 확인한다
    — 없으면(비회원) 403 `AUTH_TENANT_MISMATCH`. 성공하면 `rebind_tenant()`로
    관측성 컨텍스트의 tenant_id/actor_subject_id를 이 값으로 재바인딩한다
    (§2.1(A), tenant_binding.py)."""
    mfa_verified = _compute_mfa_verified(user)
    header_value = request.headers.get("X-Tenant-Id")
    try:
        requested_tenant_id = UUID(header_value) if header_value else None
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            {
                "error_code": ErrorCode.VALIDATION_INVALID_FIELD.value,
                "message": "X-Tenant-Id 헤더 형식이 올바르지 않습니다.",
            },
        ) from exc

    async with pool.acquire() as conn:
        try:
            context = await resolve_tenant_context(
                membership_repo,
                conn,
                user=user,
                requested_tenant_id=requested_tenant_id,
                mfa_verified=mfa_verified,
            )
        except TenantMismatchError as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {
                    "error_code": ErrorCode.AUTH_TENANT_MISMATCH.value,
                    "message": str(exc),
                },
            ) from exc

    rebind_tenant(context)
    return context
