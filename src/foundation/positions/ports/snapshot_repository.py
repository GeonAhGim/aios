"""LB-7 — Position snapshot repository port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

The domain/application layer knows only this Protocol; actual implementations
(adapters/postgres_snapshot_repository.py, LB-9) are unknown (rule 71 §4).
Only the contract that `upsert` operates via `conditional_update` (expected
`last_journal_seq`) is expressed here — actual SQL and locking belong to the
adapter.

`get` is scoped by `tenant_id` (fix for a real defect exposed by the
task-489/LB-18 cross_tenant adversarial test — previously queries used only
`position_key`, allowing any tenant who guessed another tenant's
`position_key` to read that snapshot directly). If the owner differs,
returns `None` indistinguishably from "not found" — hiding the existence of
someone else's position is the safer default."""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import PositionSnapshotView


class SnapshotPortfolioError(ValueError):
    """FA-0d-fix (task-771991202): the `portfolio_id` carried as the 5th part
    of `snapshot.position_key` cannot be written for `snapshot.tenant_id`.
    `upsert` writes that id into `pos_snapshot.portfolio_id` (a real FK) and
    fences it on tenant ownership, so a caller gets one of the two subclasses
    below instead of a driver-level FK error or a misleading
    `ConcurrencyConflictError`."""


class SnapshotPortfolioNotFoundError(SnapshotPortfolioError):
    """No `portfolio` row exists for that id -- the tenant's FA-1 default
    hierarchy was never bootstrapped (`ensure_default_hierarchy`)."""


class SnapshotPortfolioTenantMismatchError(SnapshotPortfolioError):
    """The portfolio exists but belongs to another tenant -- the write is
    rejected without touching the existing row (cross-tenant fence)."""


@runtime_checkable
class SnapshotRepository(Protocol):
    async def get(
        self, conn: asyncpg.Connection, tenant_id: UUID, position_key: str
    ) -> PositionSnapshotView | None:
        """Returns `None` when no snapshot exists yet (pre-first execution), or
        when the `position_key` exists but the owning `tenant_id` differs
        (indistinguishable from "not found" — existence is hidden)."""
        ...

    async def upsert(
        self, conn: asyncpg.Connection, snapshot: PositionSnapshotView, expected_seq: int
    ) -> PositionSnapshotView:
        """§4.3 Applies the "snapshot = fold(journal)" result conditionally.
        If the existing row's `last_journal_seq != expected_seq`, raises
        `ConcurrencyConflictError` — the caller passed a stale value without
        re-querying immediately after a journal append on the same `conn`.
        Initial upsert uses `expected_seq=0`.

        `snapshot.position_key` must be a 5-part FA-0d `PositionKey` (else
        `InvalidPositionKeyError`); its `portfolio_id` is persisted into the
        `portfolio_id` column and must belong to `snapshot.tenant_id`, else
        `SnapshotPortfolioNotFoundError` / `SnapshotPortfolioTenantMismatchError`."""
        ...

    async def list_open(
        self, conn: asyncpg.Connection, tenant_id: UUID, account_id: UUID
    ) -> list[PositionSnapshotView]:
        """All snapshots where `quantity != 0` (consumed by query leaf
        `application/queries.py`). Returns empty list when none exist."""
        ...
