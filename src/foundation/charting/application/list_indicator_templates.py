"""ListChartIndicatorTemplates query — only templates owned by the caller's
tenant (the repository query itself filters by tenant_id, so no separate
ownership check is needed here)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import indicator_template_to_view
from src.foundation.charting.contracts.v1 import ChartIndicatorTemplateView
from src.foundation.charting.ports.repository import ChartingRepository


async def list_indicator_templates(
    repo: ChartingRepository, *, tenant_id: UUID
) -> list[ChartIndicatorTemplateView]:
    templates = await repo.list_indicator_templates(tenant_id)
    return [indicator_template_to_view(template) for template in templates]
