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
        """105번 표준의 conditional_update로 상태 전이(revoke/disconnect 등
        단발 전이용). sync 경로의 CON-004 방어는 이 메서드가 아니라 아래
        `persist_snapshot_if_syncable()`이 담당한다 — 재확인과 저장 사이에
        또 다른 왕복이 끼면 이 메서드 하나만으로는 그 틈을 못 막는다."""
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

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        """CON-004 — "connection이 여전히 ACTIVE_READONLY/DEGRADED인가" 재확인과
        snapshot/health 저장(+ DEGRADED였다면 ACTIVE_READONLY로 복구)을 하나의
        트랜잭션 + row lock(`SELECT ... FOR UPDATE`)으로 묶는다. `get_connection()`
        으로 먼저 읽고 나중에 `insert_snapshot()`을 따로 호출하는 두 번의 왕복
        사이에는 revoke가 끼어들 진짜 틈(TOCTOU)이 남는다 — 이 메서드는 그 틈을
        구조적으로 없앤다. 그 사이 revoke/disconnect가 커밋됐으면
        ConcurrencyConflictError(105번 표준)."""
        ...

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None: ...

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth: ...

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None: ...
