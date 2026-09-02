"""Audit Event repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome


class AuditEventRepository(Protocol):
    async def append_event(
        self,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent:
        """79번 §1 해시 체인에 새 링크를 원자적으로 추가한다. 구현체는 같은
        tenant(또는 system) 체인에 대한 동시 append가 서로의 `previous_hash`를
        보지 못한 채 분기(fork)하지 않도록 직렬화할 책임이 있다(105번 표준의
        정신 — 다만 이건 UPDATE가 아니라 INSERT 경합이라 conditional_update가
        아니라 tenant 범위 advisory lock으로 막는다)."""
        ...

    async def list_timeline(
        self,
        tenant_id: UUID,
        *,
        cursor: str | None,
        limit: int,
        aggregate_type: str | None = None,
        action: str | None = None,
    ) -> tuple[list[AuditEvent], str | None]:
        """79번 §3 — opaque cursor 페이지네이션. 반환값은 (items, next_cursor)."""
        ...

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        """AUD-003 체인 검증용 — sequence_no 오름차순 전체. `tenant_id=None`이면
        system 체인."""
        ...
