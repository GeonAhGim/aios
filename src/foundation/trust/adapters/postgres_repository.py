"""Asyncpg implementation of TrustRepository.

Spec: AIOSproject #73 §2.1/§7, #105 (concurrency standard) — revoke_consent()
changes state only through conditional_update().

AUDIT_2026-09-30_auth_rls.md F1 (task-9453): `consent_record` is one of the
M5-enabled RLS tables (b3c7f19ad2e6) whose policy predicate is decided by the
`app.tenant_id` GUC, not by any WHERE clause in the SQL text. Every method
that reads or writes `consent_record` for a known tenant must open its
connection via [[tenant_transaction]] (same contract as
`src/foundation/connections/adapters/postgres_repository.py`) — a bare
`pool.acquire()` leaves the GUC unbound, and under the non-superuser `aios_app`
production role that makes the policy predicate false for every row,
including the owning tenant's own rows (0 rows, not an error). The explicit
`tenant_id = $1` condition in each query stays as the first line of defense
for as long as `DATABASE_URL` remains a superuser role in some environments
(RLS is not applied to superusers) — `tenant_transaction()` is the second line
that also holds once the role is non-superuser. `disclosure` has no
`tenant_id` column and is outside the M5 RLS scope, so its methods keep plain
`pool.acquire()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError, conditional_update
from src.core.db.tenant_scope import tenant_transaction
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure


def _row_to_disclosure(row: asyncpg.Record) -> Disclosure:
    return Disclosure(
        id=row["id"],
        purpose=row["purpose"],
        revision=row["revision"],
        content_hash=row["content_hash"],
        published_at=row["published_at"],
        retired_at=row["retired_at"],
    )


def _row_to_consent(row: asyncpg.Record) -> Consent:
    return Consent(
        id=row["id"],
        tenant_id=row["tenant_id"],
        subject_id=row["subject_id"],
        purpose=row["purpose"],
        disclosure_id=row["disclosure_id"],
        disclosure_revision=row["disclosure_revision"],
        state=ConsentState(row["state"]),
        accepted_at=row["accepted_at"],
        revoked_at=row["revoked_at"],
        expires_at=row["expires_at"],
    )


class PostgresTrustRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM disclosure WHERE purpose = $1 AND retired_at IS NULL "
                "ORDER BY revision DESC LIMIT 1",
                purpose,
            )
        return _row_to_disclosure(row) if row is not None else None

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> tuple[Disclosure, datetime] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT *, clock_timestamp() AS server_now FROM disclosure "
                "WHERE purpose = $1 AND revision = $2",
                purpose,
                revision,
            )
        return (_row_to_disclosure(row), row["server_now"]) if row is not None else None

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        # WHERE tenant_id = $1 stays as the first line of defense (see module
        # docstring) — tenant_transaction() binds app.tenant_id so the RLS
        # policy also holds once DATABASE_URL is a non-superuser role.
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND purpose = $2 "
                "AND state = 'ACTIVE'",
                tenant_id,
                purpose,
            )
        return _row_to_consent(row) if row is not None else None

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND purpose = $2 "
                "ORDER BY accepted_at DESC LIMIT 1",
                tenant_id,
                purpose,
            )
        return _row_to_consent(row) if row is not None else None

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        async with tenant_transaction(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                "SELECT * FROM consent_record WHERE tenant_id = $1 AND state = 'ACTIVE' "
                "ORDER BY accepted_at DESC",
                tenant_id,
            )
        return [_row_to_consent(row) for row in rows]

    async def insert_consent(
        self,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        purpose: str,
        disclosure_id: UUID,
        disclosure_revision: int,
        expires_at: datetime | None,
    ) -> Consent:
        # The partial unique index uq_consent_record_active_purpose (84b7d0faf14f)
        # prevents the DB from allowing more than one ACTIVE row per tenant/purpose —
        # this is the concurrency guard for this append-only insert path, matching
        # the 105 standard §2.2 exception: "cases where a single owner is guaranteed
        # by a schema-level UNIQUE constraint".
        async with tenant_transaction(self._pool, tenant_id) as conn:
            try:
                row = await conn.fetchrow(
                    "INSERT INTO consent_record "
                    "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision, "
                    " expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                    tenant_id,
                    subject_id,
                    purpose,
                    disclosure_id,
                    disclosure_revision,
                    expires_at,
                )
            except asyncpg.UniqueViolationError as exc:
                # Unique index violation on uq_consent_record_active_purpose —
                # between the time we checked existence (get_active_consent) and this
                # INSERT, another request created an ACTIVE consent for the same
                # tenant/purpose first. Translate this collision signal into the
                # standard exception, matching the 105 §2.2 case where a schema
                # UNIQUE constraint guarantees single ownership.
                raise ConcurrencyConflictError(
                    f"consent_record: tenant_id={tenant_id} purpose={purpose}에 대한 ACTIVE "
                    "동의가 그 사이 다른 요청으로 먼저 생성됐습니다(동시 처리 충돌)."
                ) from exc
        return _row_to_consent(row)

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        # pre_check runs inside the caller's own tenant_transaction scope, so
        # under a non-superuser role RLS already narrows "SELECT ... WHERE
        # id = $1" to rows owned by tenant_id — a row that belongs to another
        # tenant comes back as 0 rows (LookupError), not a tenant mismatch
        # (PermissionError). The explicit comparison below still raises
        # PermissionError in environments where DATABASE_URL is a superuser
        # (RLS not applied) and the row is visible regardless of tenant_id.
        async with tenant_transaction(self._pool, tenant_id) as conn:
            pre_check = await conn.fetchrow(
                "SELECT tenant_id FROM consent_record WHERE id = $1", consent_id
            )
            if pre_check is None:
                raise LookupError(f"존재하지 않는 동의입니다: {consent_id}")
            if pre_check["tenant_id"] != tenant_id:
                raise PermissionError("다른 tenant의 동의는 철회할 수 없습니다.")

            row = await conditional_update(
                conn,
                table="consent_record",
                id_column="id",
                id_value=consent_id,
                expected_state_column="state",
                expected_state_value=ConsentState.ACTIVE.value,
                set_values={
                    "state": ConsentState.REVOKED.value,
                    "revoked_at": datetime.now(timezone.utc),
                },
                extra_conditions={"tenant_id": tenant_id},
            )
        return _row_to_consent(row)
