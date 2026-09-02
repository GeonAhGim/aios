"""RevokeConnection 커맨드.

Spec: AIOSproject 74번 §2/§5.

"vault revoke"는 별도 vault 서비스가 없으므로(마이그레이션 docstring 스콥
축소) credential_binding.expires_at을 과거로 당기는 것으로 대체한다 —
평문이 애초에 저장돼 있지 않으므로(opaque encrypt(...) 참조만) 이것으로
"이후 어떤 요청도 이 binding을 유효한 것으로 취급하지 않는다"는 효과를
얻는다.
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
        raise ConnectionNotRevocableError(
            f"{connection.state.value} 상태는 해지할 수 없습니다."
        )

    revoked = await repo.transition_connection_state(
        connection_id, expected_state=connection.state.value, new_state="REVOKED"
    )
    await repo.revoke_credential_binding(connection_id)
    return connection_to_view(revoked)
