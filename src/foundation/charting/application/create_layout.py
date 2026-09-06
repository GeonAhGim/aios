"""CreateChartLayout 커맨드 — 빈 드로잉 문서를 갖는 새 레이아웃을 만든다."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.foundation.charting.application._shared import layout_to_view
from src.foundation.charting.contracts.v1 import ChartLayoutView
from src.foundation.charting.ports.repository import ChartingRepository


async def create_layout(
    repo: ChartingRepository,
    *,
    tenant_id: UUID,
    owner_subject_id: UUID,
    name: str,
    layout_state: dict[str, Any],
) -> ChartLayoutView:
    layout = await repo.create_layout(
        tenant_id=tenant_id,
        owner_subject_id=owner_subject_id,
        name=name,
        layout_state=layout_state,
    )
    return layout_to_view(layout)
