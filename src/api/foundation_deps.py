"""Shared dependencies for foundation/* routers — same pattern as src/api/suitability_deps.py."""
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
from src.foundation.charting.adapters.postgres_repository import PostgresChartingRepository
from src.foundation.charting.ports.repository import ChartingRepository
from src.foundation.connections.adapters.live_provider import LiveReadonlyAccountProvider
from src.foundation.connections.adapters.postgres_repository import PostgresConnectionRepository
from src.foundation.connections.ports.provider import ReadonlyAccountProvider
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.ports.repository import MandateRepository
from src.foundation.market_data.adapters.postgres_calendar_repository import (
    PostgresCalendarRepository,
)
from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.adapters.postgres_reference_reader import (
    PostgresReferenceReader,
)
from src.foundation.market_data.adapters.postgres_reference_repository import (
    PostgresReferenceRepository,
)
from src.foundation.market_data.adapters.postgres_tenant_venues import (
    PostgresTenantVenueSource,
)
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.entitlement import (
    EntitlementPort,
    PaperTenantVenueEntitlement,
    VenueRegistrySource,
)
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
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
from src.foundation.risk_gate.adapters.postgres_bundle_repository import (
    PostgresBundleRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.ports.repository import RiskGateRepository, RuleBundleRepository
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


def get_charting_repository(pool: asyncpg.Pool = Depends(get_pool)) -> ChartingRepository:
    return PostgresChartingRepository(pool)


def get_validation_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ValidationRepository:
    return PostgresValidationRepository(pool)


def get_risk_gate_repository(pool: asyncpg.Pool = Depends(get_pool)) -> RiskGateRepository:
    return PostgresRiskGateRepository(pool)


def get_rule_bundle_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> RuleBundleRepository:
    return PostgresBundleRepository(pool)


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


# LA-24(task-1376) — market_data read API. Adapter uses the same class as
# MarketDataQualityScheduler in main.py lifespan (single source of truth). The entitlement port
# implementation is decided only here — change only this function when swapping to DC-9 policy.
def get_candle_store(pool: asyncpg.Pool = Depends(get_pool)) -> CandleStore:
    return PostgresCandleStore(pool)


def get_market_reference_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReferenceRepository:
    return PostgresReferenceRepository(pool)


def get_market_reference_reader(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReferenceReadRepository:
    return PostgresReferenceReader(pool)


def get_market_calendar_repository(
    pool: asyncpg.Pool = Depends(get_pool),
) -> CalendarRepository:
    return PostgresCalendarRepository(pool)


def get_venue_registry_source(pool: asyncpg.Pool = Depends(get_pool)) -> VenueRegistrySource:
    return PostgresTenantVenueSource(pool)


def get_entitlement_port(
    source: VenueRegistrySource = Depends(get_venue_registry_source),
) -> EntitlementPort:
    return PaperTenantVenueEntitlement(source)


# Full-audit (agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §6) finding —
# Previously always returned FakeReadonlyAccountProvider, so the production path
# never actually touched a real exchange. Now reads the connection via the
# connection_id path parameter (FastAPI auto-binds by name) first, then
# constructs the real adapter matching provider_code (legacy CredentialResolver-based,
# adapters/live_provider.py) — Fake is no longer used anywhere in this DI path.
# For tests: call the application function directly with Fake injected (existing
# integration tests already do this), or replace this function itself via FastAPI
# `app.dependency_overrides` — this is what "only reachable via test-only
# flag" means.
async def get_readonly_account_provider(
    connection_id: UUID,
    user: User = Depends(get_current_user),
    connection_repo: ConnectionRepository = Depends(get_connection_repository),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> ReadonlyAccountProvider:
    connection = await connection_repo.get_connection(connection_id)
    if connection is None or connection.tenant_id != user.user_id:
        # Does not exist or belongs to a different tenant — 404 here. The command
        # function also re-checks the same condition (defensive duplication, #74 §5), but at
        # this point we cannot proceed anyway since we cannot create the provider.
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


# Rule 2 of #73 §6 "issued within configured step-up window" — past this window,
# even an account with mfa_enabled=True cannot be considered as "recently passed TOTP".
# Set shorter than the login session (JWT, default 60 min) — even while the session
# is alive, sensitive commands must require "recently re-verified", not just "logged in".
MFA_STEP_UP_WINDOW = timedelta(minutes=15)


def _compute_mfa_verified(user: User) -> bool:
    """Full-audit (agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) finding —
    Previously `mfa_verified = user.mfa_enabled` conflated "MFA enabled on the account"
    (account setting) with "this session recently passed TOTP" (session fact).
    Based on `mfa_verified_at` from `auth_service.py` (timestamp of TOTP pass, migration
    cdd905e63ffe), returns True only within `MFA_STEP_UP_WINDOW` — a session left open
    long after login is not considered "MFA verified" without a fresh step-up. Accounts
    with mfa_enabled=False always return False (no verification target). PLT-28 —
    `get_tenant_context` now has async I/O (header/DB), so this was extracted to a
    separate function so existing unit tests (`test_foundation_deps.py`) can still
    call it synchronously (signature stability)."""
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
    """#71 §4 "do not create in API body" — issued only via gateway auth (get_current_user).
    If no `X-Tenant-Id` header, issues a personal tenant (id == user_id) (P0 scope, see
    84b7d0faf14f migration deviation note); if present, PLT-28 `resolve_tenant_context`
    verifies active membership for that tenant — if not found (non-member), returns 403
    `AUTH_TENANT_MISMATCH`. On success, rebinds the observability context's tenant_id/
    actor_subject_id to this value via `rebind_tenant()` (§2.1(A), tenant_binding.py)."""
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
