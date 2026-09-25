"""asyncpg implementation of ConnectionRepository.

Spec: AIOSproject #74 §2/§5, #105 (concurrency standard).

task-1723 P1-D: this module was originally 330 lines (over the P6 300-line
limit) and was split by pure relocation — the snapshot/health methods were
moved to postgres_snapshot_mixin.py (the same mixin-split convention used for
KISWebSocketMixin). `PostgresConnectionRepository` inherits that mixin so the
public method set, signatures, and behavior are unchanged.

task-1718 P0-E pilot — since `account_connection` is subject to RLS
(b3c7f19ad2e6) + FORCE (c9f4e2a1b6d7), write paths where the tenant is already
known (`insert_pending_connection`/`transition_connection_state`/
`persist_snapshot_if_syncable`, the latter in postgres_snapshot_mixin.py) and
the tenant-scoped read (`list_connections`) open their connection via
`tenant_transaction()` (PLT-30, [[src/core/db/tenant_scope.py]]). This
environment's DATABASE_URL role is a superuser (rolbypassrls=true), so RLS
does not block anything right now — which is why the SQL in
`transition_connection_state`/`persist_snapshot_if_syncable` also keeps an
explicit `tenant_id` condition: until the production DSN switches to a
non-superuser role, that WHERE condition is the actual line of defense, and
`tenant_transaction()` is the second line of defense for after that switch.
`get_connection(connection_id)` was deliberately left as-is — risk_gate
(`evaluate_risk_gate.py`) also calls this method without a tenant_id, and that
is in the scope of the concurrently in-progress P0-B/P0-D (task-1715/1717), so
this leaf does not change the signature (see task note).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import conditional_update
from src.core.db.tenant_scope import tenant_transaction
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
        # Keep the WHERE tenant_id = $1 as-is — this environment's
        # DATABASE_URL role is currently a superuser (rolbypassrls=true), so
        # RLS is not applied at all (PostgreSQL does not apply RLS to
        # superusers, see task-1718 note). If we drop this WHERE and rely on
        # RLS alone, then as long as the production DSN is a superuser,
        # tenant_transaction() blocks nothing and the entire table leaks —
        # tenant_transaction() is an extra line of defense for when the
        # production DSN switches to a non-owner, non-superuser role (e.g.
        # aios_app), not a replacement for this WHERE.
        async with tenant_transaction(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM account_connection WHERE tenant_id = $1 ORDER BY created_at",
                tenant_id,
            )
        return [_row_to_connection(row) for row in rows]

    async def insert_pending_connection(
        self, connection: AccountConnection
    ) -> AccountConnection:
        async with tenant_transaction(self._pool, connection.tenant_id) as conn:
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
        tenant_id: UUID,
        expected_state: str,
        new_state: str,
    ) -> AccountConnection:
        # Add tenant_id to the WHERE explicitly via extra_conditions — this
        # environment's DATABASE_URL role is a superuser (rolbypassrls=true),
        # so RLS alone blocks nothing (task-1718 note). The RLS scope opened
        # by tenant_transaction() is an extra line of defense for when the
        # production DSN switches to a non-superuser role, and this WHERE
        # condition is the actual line of defense right now — even if the
        # caller (confirm/revoke/sync_snapshot) has already validated
        # tenant_id, if that validation is bypassed this fails closed here
        # with zero rows (no RETURNING).
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conditional_update(
                conn,
                table="account_connection",
                id_column="id",
                id_value=connection_id,
                expected_state_column="state",
                expected_state_value=expected_state,
                set_values={"state": new_state},
                extra_conditions={"tenant_id": tenant_id},
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
