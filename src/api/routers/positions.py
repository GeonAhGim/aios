"""LB-19 — positions HTTP read API (positions, journal, nav). Rule 71 §6:

The router is responsible only for auth, dependency injection, transport
validation, and query calls.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LB-19.

No write endpoints exist — journal append and snapshot updates are the
sole responsibility of application commands (LB-11 through LB-15) such as
`record_fill`, and are not exposed via HTTP. All three endpoints delegate
to LB-17 `application/queries.py`. For tenant identity, trust only the
`tenant_id` returned by PLT-28 `get_tenant_context` (the router never
accepts a tenant via query parameter). Access to another tenant's resource
must return 404 (`RESOURCE_NOT_FOUND`) as if it does not exist — the
exception class is defined in queries.py and translated to a raw HTTP
response by `exception_registry_foundation.py` (no bare HTTPException).

`GET /positions/nav` is declared before `/{position_key}/journal` — the
paths do not overlap today, but keeping static segments before dynamic
ones ensures that future endpoints will not accidentally capture `nav` as
a `position_key` value.

FA-6: an optional `portfolio_id` query parameter was added to
`GET /positions` (not a new route). Omitting it makes the response
byte-identical to the previous leaf."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.pagination import PageMeta
from src.api.deps import get_pool
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.positions import (
    NavSeriesResponse,
    PositionJournalResponse,
    PositionListResponse,
    decode_cursor,
    encode_cursor,
)
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import EntityRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_nav_repository import PostgresNavRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.queries import (
    list_journal,
    list_nav_range,
    list_positions,
)
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/positions", tags=["positions"])


def get_snapshot_repository(pool: asyncpg.Pool = Depends(get_pool)) -> SnapshotRepository:
    return PostgresSnapshotRepository(pool)


def get_journal_repository(pool: asyncpg.Pool = Depends(get_pool)) -> PositionJournalRepository:
    return PostgresJournalRepository(pool)


def get_nav_repository(pool: asyncpg.Pool = Depends(get_pool)) -> NavRepository:
    return PostgresNavRepository(pool)


def get_entity_repository(pool: asyncpg.Pool = Depends(get_pool)) -> EntityRepository:
    return PostgresEntityRepository(pool)


@router.get("")
async def get_positions(
    account_id: UUID | None = None,
    instrument_id: UUID | None = None,
    portfolio_id: UUID | None = None,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    snapshots: SnapshotRepository = Depends(get_snapshot_repository),
    entities: EntityRepository = Depends(get_entity_repository),
) -> ApiResponse[PositionListResponse]:
    items = await list_positions(
        pool,
        context.tenant_id,
        account_id=account_id,
        instrument_id=instrument_id,
        portfolio_id=portfolio_id,
        snapshots=snapshots,
        entities=entities,
    )
    return ok(PositionListResponse(items=items))


@router.get("/nav")
async def get_nav_series(
    account_id: UUID,
    start_date: date,
    end_date: date,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    nav_repo: NavRepository = Depends(get_nav_repository),
) -> ApiResponse[NavSeriesResponse]:
    series = await list_nav_range(
        pool,
        context.tenant_id,
        account_id,
        start_date=start_date,
        end_date=end_date,
        nav_repo=nav_repo,
    )
    present = {nav.nav_date for nav in series}
    span_days = (end_date - start_date).days + 1
    missing = [
        day
        for day in (start_date + timedelta(days=offset) for offset in range(span_days))
        if day not in present
    ]
    return ok(
        NavSeriesResponse(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date,
            items=series,
            missing_dates=missing,
        )
    )


@router.get("/{position_key}/journal")
async def get_position_journal(
    position_key: str,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    snapshots: SnapshotRepository = Depends(get_snapshot_repository),
    journal: PositionJournalRepository = Depends(get_journal_repository),
) -> ApiResponse[PositionJournalResponse]:
    entries, next_seq = await list_journal(
        pool,
        context.tenant_id,
        position_key,
        after_seq=decode_cursor(cursor),
        limit=limit,
        snapshots=snapshots,
        journal=journal,
    )
    page = PageMeta(
        size=limit, next_cursor=None if next_seq is None else encode_cursor(next_seq)
    )
    return ok(PositionJournalResponse(position_key=position_key, items=entries), page=page)
