"""PLT-27 — tenant/tenant_membership repository port.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2 table(line 79),
§9 PLT-27. The schema reuses `tenant`/`tenant_membership` created by PLT-26
(task-1010, f4a6b8c0d2e4) as-is — this leaf creates no new migration.

domain/application knows only this Protocol, not the actual implementation
(adapters/postgres_membership_repository.py) (§4). All methods receive an
`asyncpg.Connection` already obtained by the caller — the caller owns the
transaction boundary (`tenant_transaction()`, etc.) and this port never creates
a new connection.

Cross-tenant read/write blocking (LA-22 precedent, 190dfea) is enforced at the
signature level — `tenant_id` is a required argument, not an optional filter.
Lookups/mutations targeting a `tenant_id` owned by another tenant return `None`
or `ConcurrencyConflictError` homomorphically (§8.3 "404 homomorphism") — the
response never distinguishes "forbidden" from "not found".
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.trust.domain.models import (
    Membership,
    MembershipRole,
    MembershipState,
    Tenant,
    TenantKind,
)


@runtime_checkable
class MembershipRepository(Protocol):
    async def get_active_membership(
        self, conn: asyncpg.Connection, tenant_id: UUID, subject_id: UUID
    ) -> Membership | None:
        """Return the subject's current ACTIVE membership within the given tenant
        (at most one — guaranteed by the `uq_tenant_membership_active` partial UNIQUE
        constraint). Returns `None` homomorphically when the row does not exist or
        belongs to a different tenant."""
        ...

    async def list_memberships_for_subject(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> list[Membership]:
        """All memberships for the subject across every tenant (any state). Used by
        `resolve_tenant_context` to determine "which tenants does this user belong
        to?". Since `subject_id` is the caller's own identity, this is not a
        cross-tenant read."""
        ...

    async def count_active_owners(self, conn: asyncpg.Connection, tenant_id: UUID) -> int:
        """Count active OWNER rows while locking them with `FOR UPDATE` just before
        the last-owner check ("same transaction" — §6-5). The caller must invoke
        `update_conditional_membership_state` within the same transaction for the
        lock to be effective."""
        ...

    async def insert_membership(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        role: MembershipRole,
        created_by: UUID,
    ) -> Membership:
        """Create a new ACTIVE membership (initial grant or regrant from REVOKED —
        `revision` defaults to 1 per row since each insert creates a new row). If an
        ACTIVE row already exists for the same tenant/subject, the partial UNIQUE
        constraint `uq_tenant_membership_active` fires → `ConcurrencyConflictError`
        (implementation responsibility, §2.2 of the 105 pattern)."""
        ...

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
        """Standard-105 conditional UPDATE — all of `id`, `tenant_id`, `state`, and
        `revision` must match expected values for the transition to succeed, and
        `revision` is incremented by 1. The implementation must not distinguish
        between `tenant_id` mismatch (cross-tenant attempt) and `state`/`revision`
        mismatch (concurrency conflict) — both return `ConcurrencyConflictError`
        homomorphically (fail-closed, §8.3)."""
        ...

    async def get_personal_tenant(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> Tenant | None:
        """Look up the PERSONAL tenant for the subject. The invariant `id ==
        subject_id` holds for PERSONAL tenants (since 84b7d0faf14f, enforced by
        PLT-26 backfill). Returns `None` if no such tenant exists."""
        ...

    async def insert_tenant(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        kind: TenantKind,
        display_name: str | None = None,
    ) -> Tenant:
        """Insert a new HOUSEHOLD or ORGANIZATION tenant (consumed after PLT-28).
        `state` defaults to DB DEFAULT `ACTIVE`."""
        ...
