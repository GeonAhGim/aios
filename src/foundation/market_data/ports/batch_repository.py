"""LA-9 — port for recording lineage batches and quality issues.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §5, §9.2 LA-9,
LA-16a.

domain/application only knows this Protocol; it does not know the actual
implementation (adapters/postgres_batch_repository.py, LA-13/LA-16a) (#71 §4).
`IngestBatchResult` (LA-1 contracts) is reused as-is for the batch record
representation — since `md_ingest_batch` is INSERT only (§5), there is no
update method after `create`. `create_tick_batch`/`get_tick_batch` are the
tick-batch methods added by LA-16a (`TickIngestBatchResult`,
`md_ingest_batch_tick` — a separate table with no `timeframe`, task-656 note).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import (
    IngestBatchResult,
    QualityIssue,
    TickIngestBatchResult,
)


@runtime_checkable
class BatchRepository(Protocol):
    async def create(self, conn: asyncpg.Connection, batch: IngestBatchResult) -> IngestBatchResult:
        """`md_ingest_batch` is INSERT only (§5). Re-inserting the same
        `batch_id` raises an exception in the adapter."""
        ...

    async def add_issues(
        self, conn: asyncpg.Connection, batch_id: UUID, issues: list[QualityIssue]
    ) -> None:
        """Records to `md_quality_issue` per batch. Writes nothing if
        `issues` is empty."""
        ...

    async def get(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> IngestBatchResult | None:
        """If `tenant_id` differs from the batch owner (including
        platform-shared batches with no owner to compare against), hides
        the existence of the record and returns `None` (§8.3 LA-21 "404
        isomorphism") — the caller must not be able to distinguish a
        `None` from nonexistence versus a `None` from belonging to
        another tenant."""
        ...

    async def create_tick_batch(
        self, conn: asyncpg.Connection, batch: TickIngestBatchResult
    ) -> TickIngestBatchResult:
        """`md_ingest_batch_tick` is INSERT only. Re-inserting the same
        `batch_id` raises an exception in the adapter."""
        ...

    async def get_tick_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, tenant_id: UUID | None
    ) -> TickIngestBatchResult | None:
        """Same tenant isolation rule as `get()` (§8.3 LA-21 "404
        isomorphism")."""
        ...
