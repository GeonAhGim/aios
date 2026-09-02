"""Reconciliation & Resilience repository port. domain은 이 Protocol만 알고,
실제 구현(adapters/)은 모른다(71번 §4)."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.reconciliation.domain.models import (
    Classification,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
)


class ReconciliationRepository(Protocol):
    async def get_run_by_input_hash(
        self, target_ref: UUID, input_hash: str
    ) -> ReconciliationRun | None:
        """REC-004/006 — 같은 target+input이면 새로 계산하지 않고 이 run을
        반환한다(구현체가 items까지 채워서 반환)."""
        ...

    async def insert_run_with_items(
        self, run: ReconciliationRun, items: tuple[ReconciliationItem, ...]
    ) -> ReconciliationRun:
        """run과 item들을 하나의 트랜잭션으로 원자적으로 남긴다(80번 §2
        "persists run/items/outbox atomically")."""
        ...

    async def get_state(self, target_ref: UUID) -> ReconciliationState | None: ...

    async def list_states(self, tenant_id: UUID) -> tuple[ReconciliationState, ...]: ...

    async def upsert_state(self, state: ReconciliationState) -> ReconciliationState:
        """최초 생성이면 insert, 있으면 조건부 갱신(구현체가 105번 표준의
        revision 조건부 UPDATE를 쓴다)."""
        ...

    async def transition_state_status(
        self,
        target_ref: UUID,
        *,
        expected_revision: int,
        new_status: Classification,
        blocking_reason: str | None,
        resolved_by: UUID | None = None,
        resolution_reason: str | None = None,
    ) -> ReconciliationState:
        ...
