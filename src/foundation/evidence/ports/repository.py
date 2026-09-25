"""Audit Event repository port. The domain knows only this Protocol; actual
implementations (adapters/) remain unknown to it (Rule 71 §4)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome


@runtime_checkable
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
        """Atomically append a new link to the hash chain (Rule 79 §1). The
        implementation must serialise concurrent appends so that tenants (or
        the system chain) never fork by writing each other's
        `previous_hash` values. This follows the spirit of Rule 105 —
        however, since this is an INSERT contention (not UPDATE), use a
        tenant-scoped advisory lock instead of conditional_update."""
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
        """Opaque-cursor pagination (Rule 79 §3). Returns (items, next_cursor)."""
        ...

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        """Full sequence_no ascending list for AUD-003 chain verification.
        When `tenant_id=None`, returns the system chain."""
        ...

    async def get_latest_event(
        self, aggregate_type: str, aggregate_id: UUID, *, action: str
    ) -> AuditEvent | None:
        """Most recent event for one aggregate/action, or `None` if it has
        never happened — used by CM-5 (`mandates/application/
        activate_revision.py`) to recover "who proposed this revision"
        (`actor_subject_id`) from the existing audit trail instead of a new
        `mandate_revision.proposer_id` column (no schema change, §9 CM-5
        decision note)."""
        ...
