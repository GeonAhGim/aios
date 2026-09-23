"""Application command common helpers — ownership verification + view transformation.

`_load_owned_layout()` is the CH-5 DoD "also 404 for cross-tenant" point: it treats
a non-existent layout_id and a layout_id owned by another tenant identically,
returning the same exception/string form so the response reveals no
"exists but unauthorized" information (same pattern as reconciliation
`resolve_reconciliation.py`)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application.errors import (
    ChartIndicatorTemplateNotFoundError,
    ChartLayoutNotFoundError,
    CrossTenantChartIndicatorTemplateAccessError,
    CrossTenantChartLayoutAccessError,
)
from src.foundation.charting.contracts.v1 import (
    ChartIndicatorTemplateView,
    ChartLayoutView,
    DrawingsDocumentView,
)
from src.foundation.charting.domain.models import (
    ChartDrawingSet,
    ChartIndicatorTemplate,
    ChartLayout,
)
from src.foundation.charting.ports.repository import ChartingRepository


async def load_owned_layout(
    repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID
) -> ChartLayout:
    layout = await repo.get_layout(layout_id)
    if layout is None:
        raise ChartLayoutNotFoundError(str(layout_id))
    if layout.tenant_id != tenant_id:
        raise CrossTenantChartLayoutAccessError(str(layout_id))
    return layout


def layout_to_view(layout: ChartLayout) -> ChartLayoutView:
    return ChartLayoutView(
        id=layout.id,
        tenant_id=layout.tenant_id,
        owner_subject_id=layout.owner_subject_id,
        name=layout.name,
        layout_state=layout.layout_state,
        revision=layout.revision,
        created_at=layout.created_at,
        updated_at=layout.updated_at,
    )


def drawing_set_to_view(drawing_set: ChartDrawingSet) -> DrawingsDocumentView:
    return DrawingsDocumentView(
        layout_id=drawing_set.layout_id,
        schema_version=drawing_set.schema_version,
        drawings=list(drawing_set.drawings),
        revision=drawing_set.revision,
        updated_at=drawing_set.updated_at,
    )


async def load_owned_indicator_template(
    repo: ChartingRepository, *, tenant_id: UUID, template_id: UUID
) -> ChartIndicatorTemplate:
    """Same principle as `load_owned_layout()` — non-existent and cross-tenant
    templates raise the same exception form (both 404)."""
    template = await repo.get_indicator_template(template_id)
    if template is None:
        raise ChartIndicatorTemplateNotFoundError(str(template_id))
    if template.tenant_id != tenant_id:
        raise CrossTenantChartIndicatorTemplateAccessError(str(template_id))
    return template


def indicator_template_to_view(template: ChartIndicatorTemplate) -> ChartIndicatorTemplateView:
    return ChartIndicatorTemplateView(
        id=template.id,
        tenant_id=template.tenant_id,
        owner_subject_id=template.owner_subject_id,
        name=template.name,
        template=template.template,
        revision=template.revision,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )
