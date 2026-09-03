"""PLT-27 — tenant/tenant_membership 저장소 포트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§2 표(79행),
§9 PLT-27. 스키마는 PLT-26(task-1010, f4a6b8c0d2e4)이 만든
`tenant`/`tenant_membership`을 그대로 쓴다 — 이 리프는 새 마이그레이션을
만들지 않는다.

domain/application은 이 Protocol만 알고, 실제 구현(adapters/
postgres_membership_repository.py)은 모른다(71번 §4). 모든 메서드는
호출자가 이미 얻은 `asyncpg.Connection`을 받는다 — 트랜잭션 경계는 호출부
(`tenant_transaction()` 등)가 소유하고, 이 포트는 커넥션을 새로 만들지
않는다.

교차 테넌트 열람/변경 차단(LA-22 선례, 190dfea)은 시그니처 레벨에서
강제한다 — `tenant_id`를 생략 가능한 필터가 아니라 필수 인자로 받는다.
다른 tenant 소유 행을 가리키는 `tenant_id`로 조회/변경하면 "존재하지
않음"과 동형으로 `None` 또는 `ConcurrencyConflictError`를 돌려준다(§8.3
"404 동형") — "권한 없음"과 "존재하지 않음"을 구분해 응답하지 않는다.
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
        """해당 tenant에서 subject의 현재 ACTIVE 멤버십(있다면 정확히 하나 —
        `uq_tenant_membership_active` 부분 UNIQUE가 보장). 다른 tenant
        소유라 없으면(또는 정말 없으면) 동형으로 `None`."""
        ...

    async def list_memberships_for_subject(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> list[Membership]:
        """subject 본인이 속한 모든 tenant의 멤버십(상태 무관) —
        `resolve_tenant_context`가 "이 사용자가 어느 tenant들에 속하는가"를
        판정할 때 쓴다. subject_id는 호출자 자신의 신원이므로 교차 테넌트
        열람이 아니다."""
        ...

    async def count_active_owners(self, conn: asyncpg.Connection, tenant_id: UUID) -> int:
        """73번 §6-5 "same transaction" — last-owner 검사 직전 활성 OWNER
        행을 `FOR UPDATE`로 잠근 뒤 개수를 센다. 호출자는 이 메서드와 같은
        트랜잭션 안에서 `update_conditional_membership_state`를 이어서
        호출해야 락이 유효하다."""
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
        """새 ACTIVE 멤버십을 만든다(최초 부여 또는 REVOKED에서 regrant —
        revision은 매번 새 행이므로 DB DEFAULT 1). 같은 tenant/subject에
        이미 ACTIVE 행이 있으면 `uq_tenant_membership_active` 부분 UNIQUE
        위반 → `ConcurrencyConflictError`(구현체 책임, 105번 §2.2)."""
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
        """105번 표준 조건부 UPDATE — `id`/`tenant_id`/`state`/`revision`이
        전부 기대값과 일치해야 전이하고, `revision`을 1 증가시킨다.
        `tenant_id` 불일치(교차 테넌트 시도)와 `state`/`revision` 불일치
        (동시 경합)를 구현체가 구분해 응답하지 않는다 — 둘 다
        `ConcurrencyConflictError`로 동형 처리(fail-closed, §8.3)."""
        ...

    async def get_personal_tenant(
        self, conn: asyncpg.Connection, subject_id: UUID
    ) -> Tenant | None:
        """PERSONAL tenant는 `id == subject_id`(84b7d0faf14f 이후 불변조건,
        PLT-26 backfill이 이를 만족). 존재하지 않으면 `None`."""
        ...

    async def insert_tenant(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID,
        kind: TenantKind,
        display_name: str | None = None,
    ) -> Tenant:
        """HOUSEHOLD/ORGANIZATION tenant 신규 생성(PLT-28 이후 소비 예정).
        `state`는 DB DEFAULT `ACTIVE`."""
        ...
