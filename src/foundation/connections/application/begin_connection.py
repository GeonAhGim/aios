"""BeginConnection command — creates a connection in PENDING_CONSENT state.

Spec: AIOSproject #44 §3, #74 §2/§5.

#74 §5 "Connection commands require MFA and active ACCOUNT_READ consent" —
MFA is already proven by the caller (router) via the session itself
(same pattern as `get_tenant_context().mfa_verified` in mandates, see
foundation_deps.py). Consent reuses the existing mechanism from Trust Core
(FND-01) without introducing a separate consent flow (#71 §4 Contract
ownership — trust is the owner, connections are consumers only).
"""
from __future__ import annotations

from uuid import UUID, uuid4

from src.foundation.connections.contracts.v1 import AccountConnectionView
from src.foundation.connections.contracts.v1 import CapabilityScope as ContractScope
from src.foundation.connections.contracts.v1 import ConnectionState as ContractState
from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionConsent,
    ConnectionState,
    CredentialBinding,
)
from src.foundation.connections.domain.rules import (
    ForbiddenCapabilityScopeError,
    validate_capability_profile,
)
from src.foundation.connections.ports.repository import ConnectionRepository
from src.foundation.trust.application.evaluate_trust_freshness import evaluate_trust_freshness
from src.foundation.trust.contracts.v1 import TenantContext as TrustTenantContext
from src.foundation.trust.ports.repository import TrustRepository

ACCOUNT_READ_CONSENT_PURPOSE = "account_read_connection"

__all__ = ["ForbiddenCapabilityScopeError", "MfaRequiredError", "ConsentRequiredError"]


class MfaRequiredError(Exception):
    pass


class ConsentRequiredError(Exception):
    def __init__(self, reason_code: str | None) -> None:
        super().__init__(f"계좌 연결에는 유효한 동의가 필요합니다: {reason_code}")
        self.reason_code = reason_code


def _mask(opaque_account_ref: str) -> str:
    """#74 §4 "masked provider/account label" — opaque ref is not the provider's
    original account number, but expose only its last 4 characters anyway."""
    if len(opaque_account_ref) <= 4:
        return "*" * len(opaque_account_ref)
    return "*" * (len(opaque_account_ref) - 4) + opaque_account_ref[-4:]


def connection_to_view(
    connection: AccountConnection, binding: CredentialBinding | None = None
) -> AccountConnectionView:
    return AccountConnectionView(
        id=connection.id,
        provider_code=connection.provider_code,
        masked_account_label=_mask(connection.opaque_account_ref),
        state=ContractState(connection.state.value),
        capability_profile=[ContractScope(s.value) for s in connection.capability_profile],
        revision=connection.revision,
        created_at=connection.created_at,
        scope_verified=binding.scope_verified if binding is not None else False,
    )


async def begin_connection(
    repo: ConnectionRepository,
    trust_repo: TrustRepository,
    *,
    tenant_id: UUID,
    subject_id: UUID,
    mfa_verified: bool,
    provider_code: str,
    opaque_account_ref: str,
    requested_capability_profile: list[str],
) -> AccountConnectionView:
    if not mfa_verified:
        raise MfaRequiredError("계좌 연결은 MFA가 활성화된 세션에서만 시작할 수 있습니다.")

    scopes = validate_capability_profile(requested_capability_profile)

    trust_context = TrustTenantContext(
        tenant_id=tenant_id, subject_id=subject_id, mfa_verified=mfa_verified
    )
    freshness = await evaluate_trust_freshness(
        trust_repo, trust_context, purpose=ACCOUNT_READ_CONSENT_PURPOSE
    )
    if not freshness.is_fresh:
        raise ConsentRequiredError(freshness.reason_code)

    consent = await trust_repo.get_active_consent(tenant_id, ACCOUNT_READ_CONSENT_PURPOSE)
    if consent is None:
        raise ConsentRequiredError("POLICY_CONSENT_REQUIRED")

    connection = AccountConnection(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_subject_id=subject_id,
        provider_code=provider_code,
        opaque_account_ref=opaque_account_ref,
        state=ConnectionState.PENDING_CONSENT,
        capability_profile=scopes,
        revision=1,
    )
    created = await repo.insert_pending_connection(connection)

    await repo.insert_consent_link(
        ConnectionConsent(
            connection_id=created.id,
            consent_ref=consent.id,
            data_purposes=(ACCOUNT_READ_CONSENT_PURPOSE,),
            expires_at=consent.expires_at,
        )
    )
    return connection_to_view(created)
