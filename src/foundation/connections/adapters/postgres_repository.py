"""ConnectionRepository의 asyncpg 구현.

Spec: AIOSproject 74번 §2/§5, 105번(동시성 표준).

transition_connection_state()는 105번 표준의 conditional_update를 그대로
쓴다 — CON-004(동시 revoke와 sync 경합)는 sync_snapshot.py가 스냅샷을 쓰기
직전에 이 메서드로 "여전히 ACTIVE_READONLY/DEGRADED인가"를 재확인하는 것으로
막는다(74번 §5 "workers re-read write state immediately before ... persistence").
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    CapabilityScope,
    ConnectionConsent,
    ConnectionHealth,
    ConnectionState,
    CredentialBinding,
    CredentialClass,
    HealthState,
)


def _row_to_connection(row: asyncpg.Record) -> AccountConnection:
    return AccountConnection(
        id=row["id"],
        tenant_id=row["tenant_id"],
        owner_subject_id=row["owner_subject_id"],
        provider_code=row["provider_code"],
        opaque_account_ref=row["opaque_account_ref"],
        state=ConnectionState(row["state"]),
        capability_profile=tuple(CapabilityScope(s) for s in row["capability_profile"]),
        revision=row["revision"],
        created_at=row["created_at"],
    )


def _row_to_binding(row: asyncpg.Record) -> CredentialBinding:
    return CredentialBinding(
        id=row["id"],
        connection_id=row["connection_id"],
        vault_secret_ref=row["vault_secret_ref"],
        scope_fingerprint=row["scope_fingerprint"],
        credential_class=CredentialClass(row["credential_class"]),
        expires_at=row["expires_at"],
        rotation_state=row["rotation_state"],
    )


def _row_to_snapshot(row: asyncpg.Record) -> AccountSnapshot:
    return AccountSnapshot(
        id=row["id"],
        connection_id=row["connection_id"],
        captured_at=row["captured_at"],
        provider_as_of=row["provider_as_of"],
        freshness=row["freshness"],
        currency=row["currency"],
        source_evidence_ref=row["source_evidence_ref"],
    )


def _row_to_health(row: asyncpg.Record) -> ConnectionHealth:
    return ConnectionHealth(
        connection_id=row["connection_id"],
        evaluated_at=row["evaluated_at"],
        state=HealthState(row["state"]),
        error_code=row["error_code"],
        retry_after=row["retry_after"],
        provider_trace_ref=row["provider_trace_ref"],
    )


class PostgresConnectionRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM account_connection WHERE id = $1", connection_id
            )
        return _row_to_connection(row) if row is not None else None

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM account_connection WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return [_row_to_connection(row) for row in rows]

    async def insert_pending_connection(
        self, connection: AccountConnection
    ) -> AccountConnection:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO account_connection "
                "(tenant_id, owner_subject_id, provider_code, opaque_account_ref, "
                " state, capability_profile, revision) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
                connection.tenant_id,
                connection.owner_subject_id,
                connection.provider_code,
                connection.opaque_account_ref,
                connection.state.value,
                [s.value for s in connection.capability_profile],
                connection.revision,
            )
        return _row_to_connection(row)

    async def transition_connection_state(
        self,
        connection_id: UUID,
        *,
        expected_state: str,
        new_state: str,
    ) -> AccountConnection:
        async with self._pool.acquire() as conn:
            row = await conditional_update(
                conn,
                table="account_connection",
                id_column="id",
                id_value=connection_id,
                expected_state_column="state",
                expected_state_value=expected_state,
                set_values={"state": new_state},
            )
        return _row_to_connection(row)

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO connection_consent (connection_id, consent_ref, data_purposes, "
                " expires_at) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (connection_id) DO UPDATE SET consent_ref = EXCLUDED.consent_ref, "
                " data_purposes = EXCLUDED.data_purposes, expires_at = EXCLUDED.expires_at "
                "RETURNING *",
                link.connection_id,
                link.consent_ref,
                list(link.data_purposes),
                link.expires_at,
            )
        return ConnectionConsent(
            connection_id=row["connection_id"],
            consent_ref=row["consent_ref"],
            data_purposes=tuple(row["data_purposes"]),
            expires_at=row["expires_at"],
        )

    async def insert_credential_binding(self, binding: CredentialBinding) -> CredentialBinding:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO credential_binding (connection_id, vault_secret_ref, "
                " scope_fingerprint, credential_class, expires_at, rotation_state) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                binding.connection_id,
                binding.vault_secret_ref,
                binding.scope_fingerprint,
                binding.credential_class.value,
                binding.expires_at,
                binding.rotation_state,
            )
        return _row_to_binding(row)

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM credential_binding WHERE connection_id = $1", connection_id
            )
        return _row_to_binding(row) if row is not None else None

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE credential_binding SET expires_at = now() WHERE connection_id = $1",
                connection_id,
            )

    async def insert_snapshot(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO account_snapshot (connection_id, provider_as_of, freshness, "
                " currency, source_evidence_ref) VALUES ($1, $2, $3, $4, $5) RETURNING *",
                snapshot.connection_id,
                snapshot.provider_as_of,
                snapshot.freshness,
                snapshot.currency,
                snapshot.source_evidence_ref,
            )
        return _row_to_snapshot(row)

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM account_snapshot WHERE connection_id = $1 "
                "ORDER BY captured_at DESC LIMIT 1",
                connection_id,
            )
        return _row_to_snapshot(row) if row is not None else None

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO connection_health (connection_id, state, error_code, "
                " retry_after, provider_trace_ref) VALUES ($1, $2, $3, $4, $5) RETURNING *",
                health.connection_id,
                health.state.value,
                health.error_code,
                health.retry_after,
                health.provider_trace_ref,
            )
        return _row_to_health(row)

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM connection_health WHERE connection_id = $1 "
                "ORDER BY evaluated_at DESC LIMIT 1",
                connection_id,
            )
        return _row_to_health(row) if row is not None else None
