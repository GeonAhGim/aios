"""UpdateChartLayout 커맨드 — 낙관적 잠금(105번 표준).

호출 순서가 중요하다: 먼저 `load_owned_layout()`으로 404(없음/타 테넌트)를
가려내고, 그 다음에야 조건부 UPDATE를 시도한다 — 그래야 "타 테넌트 소유
layout_id에 잘못된 revision을 보냈을 때" 404가 나오지, 409(동시성 충돌)로
새어나가 "그 id가 존재는 한다"는 정보를 흘리지 않는다."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.foundation.charting.application._shared import layout_to_view, load_owned_layout
from src.foundation.charting.contracts.v1 import ChartLayoutView
from src.foundation.charting.ports.repository import ChartingRepository


async def update_layout(
    repo: ChartingRepository,
    *,
    tenant_id: UUID,
    layout_id: UUID,
    expected_revision: int,
    name: str | None,
    layout_state: dict[str, Any] | None,
) -> ChartLayoutView:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    updated = await repo.update_layout(
        layout_id,
        expected_revision=expected_revision,
        name=name,
        layout_state=layout_state,
    )
    return layout_to_view(updated)
