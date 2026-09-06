"""IND-12 — `GET /v1/indicators`: 3층(코어/OSS/스크립트) 지표 카탈로그 목록.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12
(선행 IND-10/task-1729).

71번 §6 규칙: 라우터는 auth/TenantContext 주입·transport validation·순수
함수(`registry_tiers.py`) 호출만 한다. 도메인 예외(`InvalidIndicatorCursorError`)
는 잡지 않는다 — EXCEPTION_MAP 전역 핸들러가 400 VALIDATION_INVALID_FIELD로
번역한다(raw HTTPException 0건, PLT-17~21 규약).

SCRIPT 층 저장소(`ScriptIndicatorSource`)는 아직 실제 구현이 없다(스크립트
지표 영속화는 이 leaf 범위 밖 — custom/dsl_indicator.py 후속) —
`NullScriptIndicatorSource`로 배선해 두고 빈 목록을 반환한다. 실제 저장소가
생기면 `get_script_indicator_source` 의존성만 교체하면 된다(테스트도
`dependency_overrides`로 동일하게 교체해 교차 테넌트 격리를 검증한다).
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
    """SCRIPT 층 저장소가 아직 없을 때의 기본 구현 — 항상 빈 목록."""

    def list_for_tenant(self, tenant_id: UUID) -> tuple[ScriptIndicatorEntry, ...]:
        del tenant_id  # Protocol 시그니처 충족용 — 저장소가 아직 없어 항상 빈 목록
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
