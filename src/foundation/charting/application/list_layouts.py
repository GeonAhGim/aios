"""ListChartLayouts query — caller sees only layouts owned by their tenant
(the repository query itself filters by tenant_id, so no separate
ownership check is needed here)."""

from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import layout_to_view
from src.foundation.charting.contracts.v1 import ChartLayoutView
from src.foundation.charting.ports.repository import ChartingRepository


async def list_layouts(repo: ChartingRepository, *, tenant_id: UUID) -> list[ChartLayoutView]:
    layouts = await repo.list_layouts(tenant_id)
    return [layout_to_view(layout) for layout in layouts]
