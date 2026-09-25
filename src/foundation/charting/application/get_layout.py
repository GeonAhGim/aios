"""GetChartLayout query — returns 404 for both not-found and cross-tenant
(`ChartLayoutNotFoundError`/`CrossTenantChartLayoutAccessError`, both
mapped to RESOURCE_NOT_FOUND)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import layout_to_view, load_owned_layout
from src.foundation.charting.contracts.v1 import ChartLayoutView
from src.foundation.charting.ports.repository import ChartingRepository


async def get_layout(
    repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID
) -> ChartLayoutView:
    layout = await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    return layout_to_view(layout)
