"""UX-8(task-7773) — `POST /v1/foundation/screener/run`: screener execution HTTP API.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2/§9 UX-6/UX-8,
frontend/e2e/journey-j2-discover-to-backtest.spec.ts:88-110 (the J2 journey's
step-1 "ghost path" gap — `apiRoutes.ts`'s `screener.run` stayed
`implemented:false` because this router itself did not exist yet).

The UX-5 contract (`contracts/v1.py`) and UX-6 execution engine
(`application/run_screen.py`) already exist — this router only does
auth/TenantContext injection and transport validation per the repo's router
convention (§6), then calls the application layer; it does not reimplement
scan/filter-evaluation/caching logic. The request body is `ScreenDefinition`
(UX-5) itself — no separate request schema is layered on top, since that
contract already carries every field and validator (non-empty `universe`,
at least one filter) the endpoint needs (see schemas/screener.py's module
docstring).

`ScreenResultCache` (UX-6) is a process-local TTL cache per that module's own
documented design, so it is kept as a single module-level singleton here
(the same convention `backtests.py`'s `get_indicator_registry` uses for a
shared `IndicatorRegistry`).

Domain exceptions (`ScreenUniverseError`/`ScreenCursorError`/
`ScreenLimitExceededError`/`ScreenTimeoutError`/`ScreenerConditionError`/
`ScreenerEvaluationError`) are never caught here — `exception_registry_foundation.py`
(`EXCEPTION_MAP`) translates them to 400/408 (zero raw `HTTPException`, PLT-21
guard).
"""

from __future__ import annotations

import logging

import asyncpg
from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.screener import ScreenRunResultView
from src.foundation.screener.adapters.postgres_field_source import PostgresScreenerFieldSource
from src.foundation.screener.application.run_screen import (
    DEFAULT_PAGE_SIZE,
    ScreenResultCache,
    run_screen,
)
from src.foundation.screener.contracts.v1 import ScreenDefinition
from src.foundation.screener.ports.field_source import ScreenerFieldSource
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/foundation/screener", tags=["screener"])

_SCREEN_RESULT_CACHE = ScreenResultCache()


def get_screener_field_source(pool: asyncpg.Pool = Depends(get_pool)) -> ScreenerFieldSource:
    return PostgresScreenerFieldSource(pool)


def get_screen_result_cache() -> ScreenResultCache:
    """Module-level singleton — see the module docstring's `IndicatorRegistry`
    parallel. Tests override this dependency to get a fresh, isolated cache."""
    return _SCREEN_RESULT_CACHE


@router.post("/run", response_model=ApiResponse[ScreenRunResultView])
async def run_screen_endpoint(
    body: ScreenDefinition,
    cursor: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    tenant: TenantContext = Depends(get_tenant_context),
    field_source: ScreenerFieldSource = Depends(get_screener_field_source),
    cache: ScreenResultCache = Depends(get_screen_result_cache),
) -> ApiResponse[ScreenRunResultView]:
    page = await run_screen(
        body,
        field_source=field_source,
        cache=cache,
        cursor=cursor,
        page_size=page_size,
    )
    logger.info(
        "screener.run",
        extra={
            "event_type": "screener.run",
            "tenant_id": str(tenant.tenant_id),
            "universe": body.universe,
            "total": page.total,
            "truncated": page.truncated,
        },
    )
    return ok(ScreenRunResultView.from_page(page))


__all__ = ["get_screen_result_cache", "get_screener_field_source", "router"]
