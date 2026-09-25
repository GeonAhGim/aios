"""U-2a — consolidated account P&L dashboard read API. §6 rule: the router
only handles auth/injection/transport validation/query dispatch.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#U-2,
ADR-2026-09-09-B Decision C.

No write endpoints — delegates only to
`src/foundation/reporting/application/account_summary.py` (read-only). The
tenant trusts only the `tenant_id` returned by PLT-28 `get_tenant_context`
(same convention as LB-19 `positions.py`). Another tenant's `account_id`
gets a 404 (`RESOURCE_NOT_FOUND`) identical to nonexistence.

This leaf narrows scope (task-2629 decision) — it returns only position
and cash aggregation. Multi-fund/portfolio scoping and exposure aggregation
are deferred to a follow-up leaf."""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import get_tenant_context
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from src.foundation.reporting.application.account_summary import get_accounts_summary
from src.foundation.reporting.contracts.v1 import AccountsSummaryResponse
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/accounts", tags=["dashboard"])


def get_snapshot_repository(pool: asyncpg.Pool = Depends(get_pool)) -> SnapshotRepository:
    return PostgresSnapshotRepository(pool)


@router.get("/summary")
async def get_accounts_summary_endpoint(
    account_id: UUID | None = None,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    snapshots: SnapshotRepository = Depends(get_snapshot_repository),
) -> ApiResponse[AccountsSummaryResponse]:
    summary = await get_accounts_summary(
        pool,
        context.tenant_id,
        account_id=account_id,
        snapshots=snapshots,
    )
    return ok(summary)
