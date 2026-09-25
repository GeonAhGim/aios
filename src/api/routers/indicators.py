"""IND-12 — `GET /v1/indicators`: 3-tier (core/OSS/script) indicator catalog list.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12
(Precedes IND-10/task-1729).

Rule 71 §6: The router performs only auth/TenantContext injection, transport validation,
and pure function calls (`registry_tiers.py`). It does not catch domain exceptions
(`InvalidIndicatorCursorError`) — the global EXCEPTION_MAP handler translates them to
400 VALIDATION_INVALID_FIELD (0 raw HTTPException, PLT-17~21 protocol).

The SCRIPT tier storage (`ScriptIndicatorSource`) has no real implementation yet
(script indicator persistence is outside this leaf scope — follow-up in
custom/dsl_indicator.py) — wired to `NullScriptIndicatorSource` which returns an
empty list. When a real storage backend appears, only the `get_script_indicator_source`
dependency needs replacement (tests also swap it via `dependency_overrides` to verify
cross-tenant isolation).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.contracts.envelope import ApiResponse, ok
from src.api.contracts.pagination import PageMeta
from src.api.foundation_deps import get_tenant_context
from src.api.schemas.indicators import IndicatorListItemView, IndicatorListView, decode_cursor
from src.core.indicators.catalog.registry_tiers import (
    DEFAULT_STATIC_CATALOG,
    ScriptIndicatorEntry,
    ScriptIndicatorSource,
    list_catalog,
    merge_script_entries,
    paginate_catalog,
)
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/indicators", tags=["indicators"])

_PAGE_MAX = 200


class NullScriptIndicatorSource:
    """Fallback when SCRIPT tier storage is unavailable — always returns empty list."""

    def list_for_tenant(self, tenant_id: UUID) -> tuple[ScriptIndicatorEntry, ...]:
        del tenant_id  # Satisfies Protocol signature — storage unavailable, returns empty list
        return ()


def get_script_indicator_source() -> ScriptIndicatorSource:
    return NullScriptIndicatorSource()


@router.get("")
async def list_indicators(
    q: str | None = Query(None, max_length=100),
    category: str | None = Query(None, max_length=100),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=_PAGE_MAX),
    context: TenantContext = Depends(get_tenant_context),
    script_source: ScriptIndicatorSource = Depends(get_script_indicator_source),
) -> ApiResponse[IndicatorListView]:
    after = decode_cursor(cursor)
    script_entries = script_source.list_for_tenant(context.tenant_id)
    catalog = merge_script_entries(
        DEFAULT_STATIC_CATALOG, script_entries, tenant_id=context.tenant_id
    )
    entries = list_catalog(catalog, q=q, category=category)
    page, next_cursor = paginate_catalog(entries, cursor=after, limit=limit)
    view = IndicatorListView(items=[IndicatorListItemView.from_entry(e) for e in page])
    return ok(view, page=PageMeta(size=limit, next_cursor=next_cursor))


__all__ = ["get_script_indicator_source", "router"]
