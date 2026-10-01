"""asyncpg implementation of `MembershipRepository` (ports/membership_repository.py).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2 table (row 80),
§9 PLT-27. The schema is `tenant`/`tenant_membership`, created by PLT-26 (f4a6b8c0d2e4).

State transitions use a single UPDATE with `id`/`tenant_id`/`state`/`revision`
all in the WHERE clause (standard-105). The shared `conditional_update` helper
only takes an id and a single state column, so it cannot also constrain
`tenant_id` — raw SQL is used here instead, the same way LC-8b
(`postgres_balance_repository.apply`) does.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.domain.models import (
    Membership,
    MembershipRole,
    MembershipState,
    Tenant,
    TenantKind,
    TenantState,
)

_MEMBERSHIP_COLUMNS = "id, tenant_id, subject_id, role, state, revision, created_at"


def _row_to_membership(row: asyncpg.Record) -> Membership:
    return Membership(
        id=row["id"],
        tenant_id=row["tenant_id"],
        subject_id=row["subject_id"],
        role=MembershipRole(row["role"]),
        state=MembershipState(row["state"]),
        revision=row["revision"],
        created_at=row["created_at"],
    )


def _row_to_tenant(row: asyncpg.Record) -> Tenant:
    return Tenant(
        id=row["id"],
        kind=TenantKind(row["kind"]),
        state=TenantState(row["state"]),
        created_at=row["created_at"],
    )


class PostgresMembershipRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_active_membership(
        self, conn: asyncpg.Connection, tenant_id: UUID, subject_id: UUID
    ) -> Membership | None:
        row = await conn.fetchrow(
            f"SELECT {_MEMBERSHIP_COLUMNS} FROM tenant_membership "  # noqa: S608
            "WHERE tenant_id = $1 AND subject_id = $2 AND state = 'ACTIVE'",
            tenant_id,
            subject_id,
        )
        return _row_to_membership(row) if row is not None else None

    async def list_memberships_for_subject(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> list[Membership]:
        rows = await conn.fetch(
            f"SELECT {_MEMBERSHIP_COLUMNS} FROM tenant_membership "  # noqa: S608
            "WHERE subject_id = $1 ORDER BY created_at",
            subject_id,
        )
        return [_row_to_membership(row) for row in rows]

    async def count_active_owners(self, conn: asyncpg.Connection, tenant_id: UUID) -> int:
        # PostgreSQL does not allow FOR UPDATE directly on an aggregate function
        # ("FOR UPDATE is not allowed with aggregate functions"), so the rows to
        # lock are selected with FOR UPDATE in a subquery first, then counted
        # outside — the locked set is the same either way.
        count = await conn.fetchval(
            "SELECT count(*) FROM ("
            "  SELECT id FROM tenant_membership "
            "  WHERE tenant_id = $1 AND state = 'ACTIVE' AND role = 'OWNER' "
            "  FOR UPDATE"
            ") locked_owners",
            tenant_id,
        )
        return int(count)

    async def insert_membership(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        role: MembershipRole,
        created_by: UUID,
    ) -> Membership:
        try:
            row = await conn.fetchrow(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, created_by) "
                f"VALUES ($1, $2, $3, $4) RETURNING {_MEMBERSHIP_COLUMNS}",  # noqa: S608
                tenant_id,
                subject_id,
                role.value,
                created_by,
            )
        except asyncpg.UniqueViolationError as exc:
            # uq_tenant_membership_active (f4a6b8c0d2e4) violation — an ACTIVE
            # membership already exists for this tenant/subject (concurrent
            # grant race or a duplicate command).
            raise ConcurrencyConflictError(
                f"tenant_membership: tenant_id={tenant_id} subject_id={subject_id}에 대한 "
                "ACTIVE 멤버십이 이미 존재합니다(동시 처리 충돌)."
            ) from exc
        return _row_to_membership(row)

    async def update_conditional_membership_state(
        self,
        conn: asyncpg.Connection,
        membership_id: UUID,
        tenant_id: UUID,
        *,
        expected_state: MembershipState,
        expected_revision: int,
        new_state: MembershipState,
    ) -> Membership:
        row = await conn.fetchrow(
            "UPDATE tenant_membership SET state = $5, revision = revision + 1, "
            "updated_at = now() "
            "WHERE id = $1 AND tenant_id = $2 AND state = $3 AND revision = $4 "
            f"RETURNING {_MEMBERSHIP_COLUMNS}",  # noqa: S608
            membership_id,
            tenant_id,
            expected_state.value,
            expected_revision,
            new_state.value,
        )
        if row is None:
            # Does not distinguish a tenant_id mismatch (cross-tenant attempt)
            # from a state/revision mismatch (concurrent race) in the response
            # — both are isomorphic (§8.3 "404 isomorphism").
            raise ConcurrencyConflictError(
                f"tenant_membership.id={membership_id}: tenant_id/state/revision이 기대와 "
                "다릅니다(동시 처리 충돌 또는 다른 tenant 소유) — 다시 조회 후 시도하세요."
            )
        return _row_to_membership(row)

    async def get_personal_tenant(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> Tenant | None:
        row = await conn.fetchrow(
            "SELECT id, kind, state, created_at FROM tenant WHERE id = $1 AND kind = 'PERSONAL'",
            subject_id,
        )
        return _row_to_tenant(row) if row is not None else None

    async def insert_tenant(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        kind: TenantKind,
        display_name: str | None = None,
    ) -> Tenant:
        row = await conn.fetchrow(
            "INSERT INTO tenant (id, kind, display_name) VALUES ($1, $2, $3) "
            "RETURNING id, kind, state, created_at",
            tenant_id,
            kind.value,
            display_name,
        )
        return _row_to_tenant(row)
