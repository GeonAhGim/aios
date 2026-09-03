"""`MembershipRepository`(ports/membership_repository.py)의 asyncpg 구현.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2 표(80행),
§9 PLT-27. 스키마는 PLT-26(f4a6b8c0d2e4)이 만든 `tenant`/`tenant_membership`.

상태 전이는 `id`/`tenant_id`/`state`/`revision`을 전부 WHERE에 건 단일
UPDATE로 처리한다(105번 표준) — 공용 `conditional_update` 헬퍼는 id·상태
컬럼 하나씩만 받아 `tenant_id`를 함께 걸 수 없으므로, LC-8b
(`postgres_balance_repository.apply`)와 같은 방식으로 직접 SQL을 쓴다.
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
        # PostgreSQL은 집계 함수에 직접 FOR UPDATE를 허용하지 않으므로("FOR UPDATE
        # is not allowed with aggregate functions"), 잠글 행을 서브쿼리에서 먼저
        # FOR UPDATE로 골라낸 뒤 바깥에서 센다 — 잠금 대상은 동일하다.
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
            # uq_tenant_membership_active(f4a6b8c0d2e4) 위반 — 이 tenant/subject에
            # 이미 ACTIVE 멤버십이 존재한다(동시 grant 경합 또는 중복 커맨드).
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
            # tenant_id 불일치(교차 테넌트 시도)와 state/revision 불일치(동시
            # 경합)를 구분해 응답하지 않는다 — 둘 다 동형(§8.3 "404 동형").
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
