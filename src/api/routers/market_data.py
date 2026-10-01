"""LA-24 — market_data HTTP read API (candle lookup/replay, instrument list/aliases).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24
(follow-up to CA decision ADR-2026-09-04-C, esc-marketdata-http-api-gap).

Rule 71 §6: the router only does auth/TenantContext injection, transport
validation, and application calls. Candle logic belongs to LA-17
(`application/get_candles`/`replay_candles`); identifier resolution,
entitlement decisions, and pagination are delegated to
`application/read_api.py`. No SQL here. Domain exceptions are not caught —
`exception_registry_foundation.py` (EXCEPTION_MAP) translates them into the
envelope (PLT-29).

The mount path is the one the frontend registry
(`frontend/packages/api-client/src/apiPaths.ts`, task-719/824) already expects:
`/v1/foundation/market-data/*` — the same namespace as other foundation
routers. Coverage decision: if a session is expected (a gap) but zero candles
are stored, the whole range is out of coverage -> 409 `DATA_COVERAGE_MISSING`
(§4.1 forbids filling with 0/NaN). A partial gap is reported via `gaps`
(same non-strict rule as LA-17). Replay is strict, so even one missing
candle is a 409.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.pagination import PageMeta
from src.api.deps import get_pool
from src.api.foundation_deps import (
    get_candle_store,
    get_entitlement_port,
    get_market_calendar_repository,
    get_market_reference_reader,
    get_market_reference_repository,
    get_tenant_context,
    get_venue_registry_source,
)
from src.api.schemas.market_data import (
    CandleSeriesView,
    EntitlementView,
    InstrumentListView,
    ReplaySeriesView,
    SymbolAliasRef,
)
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.application.get_candles import get_candles
from src.foundation.market_data.application.get_coverage import get_coverage
from src.foundation.market_data.application.read_api import (
    DataCoverageMissingError,
    authorize_feed,
    authorize_redistribution,
    authorize_venue,
    paginate_candles,
    resolve_instrument,
    validate_span,
)
from src.foundation.market_data.application.replay_candles import replay
from src.foundation.market_data.contracts.v1 import (
    Adjustment,
    CandleQuery,
    ReplayRequest,
    SeriesKey,
    SymbolStatus,
    Timeframe,
    Venue,
)
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan
from src.foundation.market_data.domain.entitlement.policy import Entitlement
from src.foundation.market_data.domain.entitlement.source_contract import DataUse
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.coverage_repository import CoverageRepository
from src.foundation.market_data.ports.entitlement import EntitlementPort, VenueRegistrySource
from src.foundation.market_data.ports.instrument_repository import InstrumentRepository
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/market-data", tags=["foundation:market-data"])

_CANDLE_PAGE_MAX = 1000
_INSTRUMENT_PAGE_MAX = 200


def get_source_contract_repository() -> SourceContractRepository:
    """DC-28 — `source_contract` is stateless (only looks up `source_id`),
    no pool injection needed. Upgrading the adapter only requires changing
    this one function (same principle as DC-27 D1)."""
    return PostgresSourceContractRepository()


def get_coverage_repository(pool: asyncpg.Pool = Depends(get_pool)) -> CoverageRepository:
    return PostgresCoverageRepository(pool)


def get_instrument_repository(pool: asyncpg.Pool = Depends(get_pool)) -> InstrumentRepository:
    return PostgresInstrumentRepository(pool)


def _entitlement_view(decision: Entitlement) -> EntitlementView:
    """`authorize_feed` already ends a denial with a 404, so any decision that
    reaches here is always an allow (mode is present) — `Entitlement`'s
    model_validator guarantees that exclusivity."""
    return EntitlementView(
        mode=decision.mode or "delayed", delayed_seconds=decision.delayed_seconds or 0
    )


@router.get("/candles")
async def get_candles_endpoint(
    venue: Venue,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    symbol: str | None = None,
    instrument_id: UUID | None = None,
    as_of: datetime | None = None,
    adjustment: Adjustment = Adjustment.RAW,
    cursor: datetime | None = None,
    limit: int = Query(500, ge=1, le=_CANDLE_PAGE_MAX),
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    store: CandleStore = Depends(get_candle_store),
    refs: ReferenceRepository = Depends(get_market_reference_repository),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    cal: CalendarRepository = Depends(get_market_calendar_repository),
    entitlement: EntitlementPort = Depends(get_entitlement_port),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[CandleSeriesView]:
    validate_span(start, end, ("as_of", as_of), ("cursor", cursor))
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn,
            refs=refs,
            reader=reader,
            venue=venue,
            symbol=symbol,
            instrument_id=instrument_id,
            now=now,
        )
        await authorize_redistribution(
            conn,
            inst.venue.value,
            repo=source_contracts,
            clock=lambda: now,
            use=DataUse.SHARED_DISPLAY,
        )
    decision = await authorize_feed(
        entitlement,
        tenant_id=context.tenant_id,
        subject_id=context.subject_id,
        inst=inst,
        timeframe=timeframe,
    )

    key = SeriesKey(venue=inst.venue, instrument_id=inst.instrument_id, timeframe=timeframe)
    query = CandleQuery(key=key, start=start, end=end, as_of=as_of, adjustment=adjustment)
    series = await get_candles(query, store=store, refs=refs, cal=cal, pool=pool)
    if not series.candles and series.gaps:
        raise DataCoverageMissingError(
            f"요청 구간 [{start.isoformat()}, {end.isoformat()})에 저장된 캔들이 없습니다."
        )

    page, next_cursor = paginate_candles(series.candles, cursor, limit)
    view = CandleSeriesView(
        key=series.key,
        candles=page,
        gaps=series.gaps,
        adjustment=series.adjustment,
        as_of=series.as_of,
        series_hash=series.series_hash,
        instrument_id=inst.instrument_id,
        symbol=symbol or inst.venue_symbol,
        canonical_symbol=inst.canonical_symbol,
        entitlement=_entitlement_view(decision),
    )
    return ok(view, page=PageMeta(size=limit, next_cursor=next_cursor))


@router.get("/candles/replay")
async def replay_candles_endpoint(
    venue: Venue,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    as_of: datetime,
    symbol: str | None = None,
    instrument_id: UUID | None = None,
    adjustment: Adjustment = Adjustment.RAW,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    store: CandleStore = Depends(get_candle_store),
    refs: ReferenceRepository = Depends(get_market_reference_repository),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    cal: CalendarRepository = Depends(get_market_calendar_repository),
    entitlement: EntitlementPort = Depends(get_entitlement_port),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[ReplaySeriesView]:
    """Delegates to LA-17 `replay` — if even one candle is missing,
    `ReplayIncompleteError` is translated into 409 `DATA_COVERAGE_MISSING`
    (strict). No pagination (A5: "same as_of + same range -> same bytes")."""
    validate_span(start, end, ("as_of", as_of))
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn,
            refs=refs,
            reader=reader,
            venue=venue,
            symbol=symbol,
            instrument_id=instrument_id,
            now=as_of,
        )
        # Redistribution scope asks "is this call allowed right now", not
        # the replay target time (as_of) — contract validity is evaluated
        # against the actual current time.
        await authorize_redistribution(
            conn,
            inst.venue.value,
            repo=source_contracts,
            clock=lambda: datetime.now(timezone.utc),
            use=DataUse.SHARED_DISPLAY,
        )
    decision = await authorize_feed(
        entitlement,
        tenant_id=context.tenant_id,
        subject_id=context.subject_id,
        inst=inst,
        timeframe=timeframe,
    )

    key = SeriesKey(venue=inst.venue, instrument_id=inst.instrument_id, timeframe=timeframe)
    request = ReplayRequest(key=key, start=start, end=end, as_of=as_of, adjustment=adjustment)
    series = await replay(request, store=store, refs=refs, cal=cal, pool=pool)
    view = ReplaySeriesView(
        key=series.key,
        candles=series.candles,
        gaps=series.gaps,
        adjustment=series.adjustment,
        as_of=series.as_of,
        series_hash=series.series_hash,
        expected_count=series.expected_count,
        missing_count=series.missing_count,
        instrument_id=inst.instrument_id,
        symbol=symbol or inst.venue_symbol,
        canonical_symbol=inst.canonical_symbol,
        entitlement=_entitlement_view(decision),
    )
    return ok(view)


@router.get("/instruments")
async def list_instruments_endpoint(
    venue: Venue | None = None,
    status: SymbolStatus | None = None,
    cursor: UUID | None = None,
    limit: int = Query(50, ge=1, le=_INSTRUMENT_PAGE_MAX),
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    source: VenueRegistrySource = Depends(get_venue_registry_source),
) -> ApiResponse[InstrumentListView]:
    """Only instruments from venues registered to this tenant. If no venue is
    registered, returns an empty list (200) — a listing has no existence-leak
    concern, so it's never a 404. `cursor` is the last `instrument_id` of the
    previous page (keyset)."""
    venues = await source.registered_venues(context.tenant_id)
    if venue is not None:
        venues = venues & {venue}
    async with pool.acquire() as conn:
        rows = await reader.list_instruments(
            conn, venues=venues, status=status, after=cursor, limit=limit + 1
        )
    items = rows[:limit]
    next_cursor = str(items[-1].instrument_id) if len(rows) > limit else None
    view = InstrumentListView(items=items, next_cursor=next_cursor)
    return ok(view, page=PageMeta(size=limit, next_cursor=next_cursor))


@router.get("/instruments/{symbol}/aliases")
async def list_aliases_endpoint(
    symbol: str,
    venue: Venue | None = None,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    refs: ReferenceRepository = Depends(get_market_reference_repository),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    source: VenueRegistrySource = Depends(get_venue_registry_source),
) -> ApiResponse[list[SymbolAliasRef]]:
    """The path segment accepts either a venue symbol (in which case the
    `venue` query param is required) or an md_instrument UUID (the frontend's
    `listInstrumentAliases(instrumentId)` sends a UUID). If it parses as a
    UUID, look up by id; otherwise look up by symbol."""
    try:
        instrument_id: UUID | None = UUID(symbol)
    except ValueError:
        instrument_id = None
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn,
            refs=refs,
            reader=reader,
            venue=venue,
            symbol=None if instrument_id else symbol,
            instrument_id=instrument_id,
            now=now,
        )
        await authorize_venue(source, tenant_id=context.tenant_id, inst=inst)
        aliases = await reader.list_aliases(conn, inst.instrument_id)
    return ok(aliases)


@router.get("/coverage")
async def get_coverage_endpoint(
    instrument_id: str,
    venue: Venue,
    timeframe: Timeframe,
    context: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    coverage_repo: CoverageRepository = Depends(get_coverage_repository),
    instrument_repo: InstrumentRepository = Depends(get_instrument_repository),
    venue_registry: VenueRegistrySource = Depends(get_venue_registry_source),
) -> ApiResponse[list[CoverageSpan]]:
    """DC-18a — merged `coverage_spans` (DC-1 ULID `instrument_id`, not the
    `md_instrument` UUID above); no coverage / no entitlement -> 200 + `[]`."""
    async with pool.acquire() as conn:
        spans = await get_coverage(
            conn,
            tenant_id=context.tenant_id,
            instrument_id=instrument_id,
            venue=venue,
            timeframe=timeframe,
            coverage_repo=coverage_repo,
            instrument_repo=instrument_repo,
            venue_registry=venue_registry,
        )
    return ok(spans)
