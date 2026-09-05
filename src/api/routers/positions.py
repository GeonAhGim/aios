"""LB-19 — positions HTTP 읽기 API(positions·journal·nav). 71번 §6 규칙:
router는 auth/주입/transport validation/query 호출만 담당한다.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LB-19.

쓰기 엔드포인트는 없다 — 저널 append·스냅샷 갱신은 `record_fill` 등
application 커맨드(LB-11~15)만의 책임이고 HTTP로 열지 않는다. 세 엔드포인트
모두 LB-17 `application/queries.py`에 위임한다. 테넌트는 PLT-28
`get_tenant_context`가 돌려준 `tenant_id`만 신뢰한다(쿼리 파라미터로
tenant를 받지 않는다). 타 테넌트 리소스는 존재하지 않는 것과 같은 404
(`RESOURCE_NOT_FOUND`)로 응답한다 — 예외 클래스는 queries.py가 정의하고
`exception_registry_foundation.py`가 봉투로 번역한다(raw HTTPException 없음).

`GET /positions/nav`는 `/{position_key}/journal`보다 먼저 선언한다 —
지금은 경로가 겹치지 않지만, 정적 세그먼트가 동적 세그먼트보다 앞서는
관례를 유지해 후속 엔드포인트가 추가돼도 `nav`가 position_key로 잡히지
않게 한다."""
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


@router.get("")
async def get_positions(
    account_id: UUID | None = None,
    instrument_id: UUID | None = None,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    snapshots: SnapshotRepository = Depends(get_snapshot_repository),
) -> ApiResponse[PositionListResponse]:
    items = await list_positions(
        pool,
        context.tenant_id,
        account_id=account_id,
        instrument_id=instrument_id,
        snapshots=snapshots,
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
