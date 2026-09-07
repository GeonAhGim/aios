"""CreateChartIndicatorTemplate command — `(tenant_id, name)` duplicate is
409.

The repository translates a DB UNIQUE(tenant_id, name) violation into
`ConcurrencyConflictError` (same pattern as
trust/postgres_membership_repository.py; no new taxonomy — task-1904
decision)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.foundation.charting.application._shared import indicator_template_to_view
from src.foundation.charting.contracts.v1 import ChartIndicatorTemplateView
from src.foundation.charting.ports.repository import ChartingRepository


async def create_indicator_template(
    repo: ChartingRepository,
    *,
    tenant_id: UUID,
    owner_subject_id: UUID,
    name: str,
    template: dict[str, Any],
) -> ChartIndicatorTemplateView:
    created = await repo.create_indicator_template(
        tenant_id=tenant_id,
        owner_subject_id=owner_subject_id,
        name=name,
        template=template,
    )
    return indicator_template_to_view(created)
