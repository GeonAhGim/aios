"""LA-24 — market_data HTTP 읽기 API(캔들 조회/리플레이, 인스트루먼트 목록/별칭).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24
(CA 결정 ADR-2026-09-04-C 후속, esc-marketdata-http-api-gap).

71번 §6 규칙: 라우터는 auth/TenantContext 주입·transport validation·application
호출만 한다. 캔들 로직은 LA-17(`application/get_candles`·`replay_candles`),
식별자 해석·이용권 판정·페이지 분할은 `application/read_api.py`에 위임하고
SQL은 없다. 도메인 예외는 잡지 않는다 — `exception_registry_foundation.py`
(EXCEPTION_MAP)가 봉투로 번역한다(PLT-29).

마운트 경로는 프론트 레지스트리(`frontend/packages/api-client/src/apiPaths.ts`
task-719/824)가 이미 기대하던 `/v1/foundation/market-data/*`다 — 다른
foundation 라우터와 같은 네임스페이스. 커버리지 판정: 기대 세션(갭)은
있는데 저장 캔들이 0건이면 구간 전체가 커버리지 밖 → 409
`DATA_COVERAGE_MISSING`(§4.1 0/NaN 채움 금지). 일부만 비면 `gaps`로 알린다
(LA-17 비-strict 규칙 그대로). 리플레이는 strict라 결측 하나도 409다.
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
from src.foundation.market_data.application.get_candles import get_candles
from src.foundation.market_data.application.read_api import (
    DataCoverageMissingError,
    authorize_feed,
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
from src.foundation.market_data.domain.entitlement.policy import Entitlement
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.entitlement import EntitlementPort, VenueRegistrySource
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/market-data", tags=["foundation:market-data"])

_CANDLE_PAGE_MAX = 1000
_INSTRUMENT_PAGE_MAX = 200


def _entitlement_view(decision: Entitlement) -> EntitlementView:
    """`authorize_feed`가 거부를 이미 404로 끝냈으므로 여기 오는 결정은 항상
    허용(mode 존재)이다 — `Entitlement`의 model_validator가 그 배타성을 보장한다."""
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
) -> ApiResponse[CandleSeriesView]:
    validate_span(start, end, ("as_of", as_of), ("cursor", cursor))
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn, refs=refs, reader=reader, venue=venue, symbol=symbol,
            instrument_id=instrument_id, now=now,
        )
    decision = await authorize_feed(
        entitlement, tenant_id=context.tenant_id, subject_id=context.subject_id,
        inst=inst, timeframe=timeframe,
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
        key=series.key, candles=page, gaps=series.gaps, adjustment=series.adjustment,
        as_of=series.as_of, series_hash=series.series_hash,
        instrument_id=inst.instrument_id, symbol=symbol or inst.venue_symbol,
        canonical_symbol=inst.canonical_symbol, entitlement=_entitlement_view(decision),
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
) -> ApiResponse[ReplaySeriesView]:
    """LA-17 `replay` 위임 — 결측이 하나라도 있으면 `ReplayIncompleteError`가
    409 `DATA_COVERAGE_MISSING`으로 번역된다(strict). 페이지네이션 없음(A5
    "같은 as_of+같은 범위 → 같은 바이트")."""
    validate_span(start, end, ("as_of", as_of))
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn, refs=refs, reader=reader, venue=venue, symbol=symbol,
            instrument_id=instrument_id, now=as_of,
        )
    decision = await authorize_feed(
        entitlement, tenant_id=context.tenant_id, subject_id=context.subject_id,
        inst=inst, timeframe=timeframe,
    )

    key = SeriesKey(venue=inst.venue, instrument_id=inst.instrument_id, timeframe=timeframe)
    request = ReplayRequest(key=key, start=start, end=end, as_of=as_of, adjustment=adjustment)
    series = await replay(request, store=store, refs=refs, cal=cal, pool=pool)
    view = ReplaySeriesView(
        key=series.key, candles=series.candles, gaps=series.gaps,
        adjustment=series.adjustment, as_of=series.as_of, series_hash=series.series_hash,
        expected_count=series.expected_count, missing_count=series.missing_count,
        instrument_id=inst.instrument_id, symbol=symbol or inst.venue_symbol,
        canonical_symbol=inst.canonical_symbol, entitlement=_entitlement_view(decision),
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
    """자기 테넌트가 등록한 벤처의 인스트루먼트만. 등록 벤처가 없으면 빈
    목록(200) — 목록은 존재 누설 문제가 없어 404가 아니다. `cursor`는 직전
    페이지 마지막 `instrument_id`(keyset)."""
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
    """경로 세그먼트는 벤처 심볼(이때 `venue` 쿼리 필수) 또는 md_instrument
    UUID 둘 다 받는다(프론트 `listInstrumentAliases(instrumentId)`는 UUID를
    보낸다). UUID 형식이면 id 조회, 아니면 심볼 조회."""
    try:
        instrument_id: UUID | None = UUID(symbol)
    except ValueError:
        instrument_id = None
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        inst = await resolve_instrument(
            conn, refs=refs, reader=reader, venue=venue,
            symbol=None if instrument_id else symbol, instrument_id=instrument_id, now=now,
        )
        await authorize_venue(source, tenant_id=context.tenant_id, inst=inst)
        aliases = await reader.list_aliases(conn, inst.instrument_id)
    return ok(aliases)
