"""DeleteChartIndicatorTemplate command — verifies ownership before delete
(both nonexistence and another tenant's ownership are 404, same principle
as delete_layout.py)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import load_owned_indicator_template
from src.foundation.charting.ports.repository import ChartingRepository


async def delete_indicator_template(
    repo: ChartingRepository, *, tenant_id: UUID, template_id: UUID
) -> None:
    await load_owned_indicator_template(repo, tenant_id=tenant_id, template_id=template_id)
    await repo.delete_indicator_template(template_id)
