"""GetChartLayout 쿼리 — 존재하지 않거나 타 테넌트 소유면 둘 다 404
(`ChartLayoutNotFoundError`/`CrossTenantChartLayoutAccessError`, 둘 다
RESOURCE_NOT_FOUND로 매핑)."""
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
