"""Charting API 라우터 — CH-5.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5. 71번 §6 규칙: router는 auth/`TenantContext` 주입(PLT-28
`get_tenant_context`)/transport validation/command invocation만 담당한다.
`X-Tenant-Id`는 직접 읽지 않는다 — cross-tenant 시도는 그 의존성 단계에서
이미 403 `AUTH_TENANT_MISMATCH`로 막힌다(trust_memberships.py와 동일 패턴).

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(404/409는 기존 코드
재사용, task-1557 decision)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.foundation_deps import get_charting_repository, get_tenant_context
from src.foundation.charting.application.create_indicator_template import (
    create_indicator_template,
)
from src.foundation.charting.application.create_layout import create_layout
from src.foundation.charting.application.delete_indicator_template import (
    delete_indicator_template,
)
from src.foundation.charting.application.delete_layout import delete_layout
from src.foundation.charting.application.get_drawings import get_drawings
from src.foundation.charting.application.get_indicator_template import get_indicator_template
from src.foundation.charting.application.get_layout import get_layout
from src.foundation.charting.application.list_indicator_templates import (
    list_indicator_templates,
)
from src.foundation.charting.application.list_layouts import list_layouts
from src.foundation.charting.application.put_drawings import put_drawings
from src.foundation.charting.application.update_layout import update_layout
from src.foundation.charting.contracts.v1 import (
    ChartIndicatorTemplateView,
    ChartLayoutView,
    CreateChartIndicatorTemplateRequest,
    CreateChartLayoutRequest,
    DrawingsDocumentView,
    PutDrawingsRequest,
    UpdateChartLayoutRequest,
)
from src.foundation.charting.ports.repository import ChartingRepository
from src.foundation.trust.contracts.v1 import TenantContext

router = APIRouter(prefix="/v1/foundation/charting", tags=["foundation:charting"])


@router.post("/layouts", status_code=status.HTTP_201_CREATED)
async def post_create_layout(
    body: CreateChartLayoutRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[ChartLayoutView]:
    result = await create_layout(
        repo,
        tenant_id=context.tenant_id,
        owner_subject_id=context.subject_id,
        name=body.name,
        layout_state=body.layout_state,
    )
    return ok(result)


@router.get("/layouts")
async def get_list_layouts(
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[list[ChartLayoutView]]:
    result = await list_layouts(repo, tenant_id=context.tenant_id)
    return ok(result)


@router.get("/layouts/{layout_id}")
async def get_layout_by_id(
    layout_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[ChartLayoutView]:
    result = await get_layout(repo, tenant_id=context.tenant_id, layout_id=layout_id)
    return ok(result)


@router.patch("/layouts/{layout_id}")
async def patch_update_layout(
    layout_id: UUID,
    body: UpdateChartLayoutRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[ChartLayoutView]:
    result = await update_layout(
        repo,
        tenant_id=context.tenant_id,
        layout_id=layout_id,
        expected_revision=body.expected_revision,
        name=body.name,
        layout_state=body.layout_state,
    )
    return ok(result)


@router.delete("/layouts/{layout_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_layout_by_id(
    layout_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> None:
    await delete_layout(repo, tenant_id=context.tenant_id, layout_id=layout_id)


@router.get("/layouts/{layout_id}/drawings")
async def get_layout_drawings(
    layout_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[DrawingsDocumentView]:
    result = await get_drawings(repo, tenant_id=context.tenant_id, layout_id=layout_id)
    return ok(result)


@router.put("/layouts/{layout_id}/drawings")
async def put_layout_drawings(
    layout_id: UUID,
    body: PutDrawingsRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[DrawingsDocumentView]:
    result = await put_drawings(
        repo,
        tenant_id=context.tenant_id,
        layout_id=layout_id,
        expected_revision=body.expected_revision,
        schema_version=body.schema_version,
        drawings=body.drawings,
    )
    return ok(result)


@router.post("/indicator-templates", status_code=status.HTTP_201_CREATED)
async def post_create_indicator_template(
    body: CreateChartIndicatorTemplateRequest,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[ChartIndicatorTemplateView]:
    result = await create_indicator_template(
        repo,
        tenant_id=context.tenant_id,
        owner_subject_id=context.subject_id,
        name=body.name,
        template=body.template,
    )
    return ok(result)


@router.get("/indicator-templates")
async def get_list_indicator_templates(
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[list[ChartIndicatorTemplateView]]:
    result = await list_indicator_templates(repo, tenant_id=context.tenant_id)
    return ok(result)


@router.get("/indicator-templates/{template_id}")
async def get_indicator_template_by_id(
    template_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> ApiResponse[ChartIndicatorTemplateView]:
    result = await get_indicator_template(
        repo, tenant_id=context.tenant_id, template_id=template_id
    )
    return ok(result)


@router.delete("/indicator-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_indicator_template_by_id(
    template_id: UUID,
    context: TenantContext = Depends(get_tenant_context),
    repo: ChartingRepository = Depends(get_charting_repository),
) -> None:
    await delete_indicator_template(repo, tenant_id=context.tenant_id, template_id=template_id)
