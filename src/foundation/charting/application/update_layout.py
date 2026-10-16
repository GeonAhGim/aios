"""UpdateChartLayout command — Optimistic lock (standard-105 pattern).

Call order matters: first filter out 404 (missing/other tenant) via
`load_owned_layout()`, then attempt the conditional UPDATE — this ensures
that sending a stale revision for another tenant's layout_id returns 404
instead of leaking the fact that the ID exists via 409 (concurrency conflict)."""

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
        tenant_id=tenant_id,
        expected_revision=expected_revision,
        name=name,
        layout_state=layout_state,
    )
    return layout_to_view(updated)
