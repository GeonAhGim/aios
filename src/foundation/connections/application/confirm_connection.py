"""ConfirmConnection(+VerifyScope) command.

Spec: AIOSproject #74 §2/§3.

Scope reduction (explicit, see migration docstring): #74 §2 splits
PENDING_CONSENT -> CONNECTING (ConfirmConnection/owner) and
CONNECTING -> ACTIVE_READONLY (VerifyScope/service) into separate commands/actors,
but this leaf performs both transitions sequentially within a single command — the
real provider's async browser handshake is absent (only a fake adapter exists), so
the two state values remain as-is per #74 §2, allowing the provider to split this
function into two commands when it attaches.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from src.core.security.encryption import legacy_encrypt
from src.foundation.connections.application.begin_connection import connection_to_view
from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.contracts.v1 import AccountConnectionView
from src.foundation.connections.domain.models import CredentialBinding, CredentialClass
from src.foundation.connections.domain.rules import (
    ForbiddenCapabilityScopeError,
    compute_scope_fingerprint,
    detect_scope_drift,
)
from src.foundation.connections.ports.provider import ReadonlyAccountProvider, SecretLease
from src.foundation.connections.ports.repository import ConnectionRepository

__all__ = [
    "ConnectionNotFoundError",
    "CrossTenantConnectionAccessError",
    "ForbiddenCapabilityScopeError",
    "ScopeVerificationFailedError",
]


class ScopeVerificationFailedError(Exception):
    pass


async def confirm_connection(
    repo: ConnectionRepository,
    provider: ReadonlyAccountProvider,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    encryption_key: str,
) -> AccountConnectionView:
    connection = await repo.get_connection(connection_id)
    if connection is None:
        raise ConnectionNotFoundError(str(connection_id))
    if connection.tenant_id != tenant_id:
        raise CrossTenantConnectionAccessError(str(connection_id))

    # PENDING_CONSENT -> CONNECTING. Propagate ConcurrencyConflictError as-is
    # if the state differs from expected (e.g., already advanced past CONNECTING).
    await repo.transition_connection_state(
        connection_id,
        tenant_id=tenant_id,
        expected_state="PENDING_CONSENT",
        new_state="CONNECTING",
    )

    try:
        proof = await provider.verify_readonly_scope(SecretLease(lease_ref=f"lease-{uuid4().hex}"))
    except Exception as exc:
        raise ScopeVerificationFailedError(str(exc)) from exc

    if detect_scope_drift(connection.capability_profile, proof.granted_scopes):
        granted_values = [s.value for s in proof.granted_scopes]
        rejected = [
            v for v in granted_values if v not in {s.value for s in connection.capability_profile}
        ]
        raise ForbiddenCapabilityScopeError(rejected=rejected or granted_values)

    vault_secret_ref = legacy_encrypt(proof.provider_credential_ref, encryption_key)
    binding = await repo.insert_credential_binding(
        CredentialBinding(
            id=uuid4(),
            connection_id=connection_id,
            vault_secret_ref=vault_secret_ref,
            scope_fingerprint=compute_scope_fingerprint(connection.capability_profile),
            credential_class=CredentialClass.READONLY,
            expires_at=None,
            scope_verified=proof.provider_verified,
        )
    )

    activated = await repo.transition_connection_state(
        connection_id,
        tenant_id=tenant_id,
        expected_state="CONNECTING",
        new_state="ACTIVE_READONLY",
    )
    return connection_to_view(activated, binding)
