"""DeleteChartLayout 커맨드 — `chart_drawing_set`은 FK `ON DELETE CASCADE`로
함께 지워진다."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import load_owned_layout
from src.foundation.charting.ports.repository import ChartingRepository


async def delete_layout(repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID) -> None:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    await repo.delete_layout(layout_id)
