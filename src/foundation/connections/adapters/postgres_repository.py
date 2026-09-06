"""ConnectionRepository의 asyncpg 구현.

Spec: AIOSproject 74번 §2/§5, 105번(동시성 표준).

task-1723 P1-D: 원래 330줄(P6 300줄 초과)이던 이 모듈을 순수 이동으로
분할했다 — 스냅샷/헬스 관련 메서드는 postgres_snapshot_mixin.py로
분리(KISWebSocketMixin과 동일한 믹스인 분리 관례). `PostgresConnectionRepository`가
그 믹스인을 상속해 공개 메서드 집합·시그니처·동작은 그대로 유지한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.foundation.connections.adapters.postgres_snapshot_mixin import (
    _row_to_health,
    _row_to_snapshot,
    _SnapshotHealthMixin,
)
from src.foundation.connections.domain.models import (
    AccountConnection,
    CapabilityScope,
    ConnectionConsent,
    ConnectionState,
    CredentialBinding,
    CredentialClass,
)

__all__ = [
    "PostgresConnectionRepository",
    "_row_to_binding",
    "_row_to_connection",
    "_row_to_health",
    "_row_to_snapshot",
]


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
        scope_verified=row["scope_verified"],
    )


class PostgresConnectionRepository(_SnapshotHealthMixin):
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
                " scope_fingerprint, credential_class, expires_at, rotation_state, "
                " scope_verified) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
                binding.connection_id,
                binding.vault_secret_ref,
                binding.scope_fingerprint,
                binding.credential_class.value,
                binding.expires_at,
                binding.rotation_state,
                binding.scope_verified,
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
