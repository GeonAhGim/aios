"""CreateChartIndicatorTemplate 커맨드 — `(tenant_id, name)` 중복은 409.

DB UNIQUE(tenant_id, name) 위반을 저장소가 `ConcurrencyConflictError`로
번역한다(trust/postgres_membership_repository.py와 동일 패턴, 새 taxonomy
발명 금지 — task-1904 decision)."""
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
