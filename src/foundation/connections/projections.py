"""ConnectionListView 조립 — 74번 §4 GET 목록 화면용.

71번 §4 "read model may lag" — FND-01/02와 동일하게 지금은 프로젝션 워커
없이 같은 DB를 직접 읽으므로 지연이 없지만, `as_of`는 항상 포함한다(108번 §2).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.connections.application.begin_connection import connection_to_view
from src.foundation.connections.contracts.v1 import AccountConnectionView
from src.foundation.connections.ports.repository import ConnectionRepository


class ConnectionListView:
    def __init__(self, connections: list[AccountConnectionView], as_of: datetime) -> None:
        self.connections = connections
        self.as_of = as_of


async def build_connection_list_view(
    repo: ConnectionRepository, tenant_id: UUID
) -> ConnectionListView:
    connections = await repo.list_connections(tenant_id)
    views = []
    for c in connections:
        binding = await repo.get_credential_binding(c.id)
        views.append(connection_to_view(c, binding))
    return ConnectionListView(connections=views, as_of=datetime.now(timezone.utc))
