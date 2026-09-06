"""ListChartLayouts 쿼리 — 호출자 tenant 소유 레이아웃만(저장소 쿼리 자체가
tenant_id로 필터하므로 여기서 별도 소유권 검사가 필요 없다)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import layout_to_view
from src.foundation.charting.contracts.v1 import ChartLayoutView
from src.foundation.charting.ports.repository import ChartingRepository


async def list_layouts(repo: ChartingRepository, *, tenant_id: UUID) -> list[ChartLayoutView]:
    layouts = await repo.list_layouts(tenant_id)
    return [layout_to_view(layout) for layout in layouts]
