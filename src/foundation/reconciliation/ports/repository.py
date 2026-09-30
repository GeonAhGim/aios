"""Reconciliation & Resilience repository port.

domain is aware of only this Protocol; actual implementation (adapters/) is
unknown (§4, page 71).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.reconciliation.domain.models import (
    Classification,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
)


@runtime_checkable
class ReconciliationRepository(Protocol):
    async def get_run_by_input_hash(
        self, target_ref: UUID, input_hash: str, tenant_id: UUID
    ) -> ReconciliationRun | None:
        """REC-004/006 — For the same target+input, return this run instead of
        recomputing; implementation must populate items.

        `tenant_id` scopes the read against `reconciliation_run`'s RLS policy
        (F1, task-9456) — every call site already knows the caller's tenant.
        """
        ...

    async def insert_run_with_items(
        self, run: ReconciliationRun, items: tuple[ReconciliationItem, ...]
    ) -> ReconciliationRun:
        """Persist run and items atomically in a single transaction (§2, page 80).

        Raises `ReconciliationRunAlreadyExists` (domain/models.py) if
        `UNIQUE(target_ref, input_hash)` already holds a row a concurrent
        caller with the same input committed first — the exception carries
        that row so the caller can reuse it (REC-004).
        """
        ...

    async def get_state(self, target_ref: UUID) -> ReconciliationState | None: ...

    async def list_states(self, tenant_id: UUID) -> tuple[ReconciliationState, ...]: ...

    async def upsert_state(self, state: ReconciliationState) -> ReconciliationState:
        """Insert on first creation; perform conditional update if exists
        (implementation uses revision-conditional UPDATE per standard-105).
        """
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
    ) -> ReconciliationState: ...
