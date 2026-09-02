"""Connected Asset repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    ConnectionConsent,
    ConnectionHealth,
    CredentialBinding,
)


class ConnectionRepository(Protocol):
    async def get_connection(self, connection_id: UUID) -> AccountConnection | None: ...

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]: ...

    async def insert_pending_connection(
        self, connection: AccountConnection
    ) -> AccountConnection: ...

    async def transition_connection_state(
        self,
        connection_id: UUID,
        *,
        expected_state: str,
        new_state: str,
    ) -> AccountConnection:
        """105번 표준의 conditional_update로 상태 전이. 대상이 기대 상태가
        아니면 ConcurrencyConflictError(구현체 책임) — CON-004(동시 revoke와
        sync 경합)가 이 조건에 기댄다."""
        ...

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent: ...

    async def insert_credential_binding(
        self, binding: CredentialBinding
    ) -> CredentialBinding: ...

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None: ...

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        """vault_secret_ref가 가리키는 값 자체는 여기서 지우지 않는다(감사
        추적 보존, 49번 원칙) — expires_at을 과거로 당겨 재사용을 막는다."""
        ...

    async def insert_snapshot(self, snapshot: AccountSnapshot) -> AccountSnapshot: ...

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None: ...

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth: ...

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None: ...
