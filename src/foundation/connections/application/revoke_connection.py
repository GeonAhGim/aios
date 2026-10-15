"""RevokeConnection command.

Spec: AIOSproject #74 §2/§5.

Since there is no separate vault service for "vault revoke" (migration docstring
scope reduction), we replace it by setting credential_binding.expires_at to a
past time — because no plaintext is stored in the first place (only opaque
encrypt(...) references), this achieves the effect of
"no subsequent request shall treat this binding as valid."
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.connections.application.begin_connection import connection_to_view
from src.foundation.connections.application.errors import (
    ConnectionNotFoundError,
    CrossTenantConnectionAccessError,
)
from src.foundation.connections.contracts.v1 import AccountConnectionView
from src.foundation.connections.domain.models import ConnectionState
from src.foundation.connections.ports.repository import ConnectionRepository

_REVOCABLE_STATES = frozenset({ConnectionState.ACTIVE_READONLY, ConnectionState.DEGRADED})


class ConnectionNotRevocableError(Exception):
    pass


async def revoke_connection(
    repo: ConnectionRepository,
    *,
    tenant_id: UUID,
    connection_id: UUID,
) -> AccountConnectionView:
    connection = await repo.get_connection(connection_id)
    if connection is None:
        raise ConnectionNotFoundError(str(connection_id))
    if connection.tenant_id != tenant_id:
        raise CrossTenantConnectionAccessError(str(connection_id))
    if connection.state not in _REVOCABLE_STATES:
        raise ConnectionNotRevocableError(f"{connection.state.value} 상태는 해지할 수 없습니다.")

    revoked = await repo.transition_connection_state(
        connection_id,
        tenant_id=tenant_id,
        expected_state=connection.state.value,
        new_state="REVOKED",
    )
    await repo.revoke_credential_binding(connection_id)
    return connection_to_view(revoked)
