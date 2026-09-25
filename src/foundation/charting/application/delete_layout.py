"""DeleteChartLayout command — `chart_drawing_set` rows are deleted via FK
`ON DELETE CASCADE` when the layout is removed."""

from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import load_owned_layout
from src.foundation.charting.ports.repository import ChartingRepository


async def delete_layout(repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID) -> None:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    await repo.delete_layout(layout_id, tenant_id=tenant_id)
