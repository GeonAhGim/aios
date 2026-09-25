"""Connected Asset repository port. The domain knows only this Protocol;
actual implementations (adapters/) remain hidden(71 §4)."""
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
        tenant_id: UUID,
        expected_state: str,
        new_state: str,
    ) -> AccountConnection:
        """State transition via conditional_update per standard-105 (for
        one-shot transitions such as revoke/disconnect). CON-004 defense on
        the sync path is handled by `persist_snapshot_if_syncable()` below,
        not this method — a round-trp between re-confirmation and storage
        cannot be closed by this method alone.
        task-1718 P0-E — `tenant_id` opens `tenant_transaction()` and is
        explicitly included in the WHERE clause (see adapters/postgres_repository.py):
        even if the caller's tenant check is bypassed, this method blocks it
        by matching zero rows."""
        ...

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent: ...

    async def insert_credential_binding(
        self, binding: CredentialBinding
    ) -> CredentialBinding: ...

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None: ...

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        """Do not erase the value pointed to by vault_secret_ref here
        (preserve audit trail, principle-49) — pull expires_at into the past
        to prevent reuse."""
        ...

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        tenant_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        """CON-004 — re-confirm "is the connection still ACTIVE_READONLY/DEGRADED?"
        and bundle snapshot/health storage (+ recovery from DEGRADED to
        ACTIVE_READONLY) in a single transaction + row lock (`SELECT ... FOR UPDATE`).
        Between an initial read via `get_connection()` and a separate
        `insert_snapshot()` call, a revoke could slip through (TOCTOU gap) —
        this method eliminates that gap structurally. If revoke/disconnect
        committed in between, raise ConcurrencyConflictError(standard-105).
        task-1718 P0-E — `tenant_id` opens `tenant_transaction()` and is
        explicitly included in the WHERE clause of the re-confirmation SELECT."""
        ...

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None: ...

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth: ...

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None: ...
