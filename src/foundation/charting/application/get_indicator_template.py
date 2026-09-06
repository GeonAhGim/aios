"""GetChartIndicatorTemplate 쿼리 — 미존재/타 테넌트 소유 둘 다 404
(`ChartIndicatorTemplateNotFoundError`/`CrossTenantChartIndicatorTemplateAccessError`,
둘 다 RESOURCE_NOT_FOUND로 매핑)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import (
    indicator_template_to_view,
    load_owned_indicator_template,
)
from src.foundation.charting.contracts.v1 import ChartIndicatorTemplateView
from src.foundation.charting.ports.repository import ChartingRepository


async def get_indicator_template(
    repo: ChartingRepository, *, tenant_id: UUID, template_id: UUID
) -> ChartIndicatorTemplateView:
    template = await load_owned_indicator_template(
        repo, tenant_id=tenant_id, template_id=template_id
    )
    return indicator_template_to_view(template)
