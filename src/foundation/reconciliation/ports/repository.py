"""Reconciliation & Resilience repository port.

domain is aware of only this Protocol; actual implementation (adapters/) is
unknown (§4, page 71).
"""
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
        """REC-004/006 — For the same target+input, return this run instead of
        recomputing; implementation must populate items.
        """
        ...

    async def insert_run_with_items(
        self, run: ReconciliationRun, items: tuple[ReconciliationItem, ...]
    ) -> ReconciliationRun:
        """Persist run and items atomically in a single transaction (§2, page 80)."""
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
    ) -> ReconciliationState:
        ...
