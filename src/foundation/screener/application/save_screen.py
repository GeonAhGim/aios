"""UX-7 — save a screen definition (validates before persisting).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/{save_screen,share_screen,alert_on_screen}.py` (save).

`SavedScreenerRepository.save()` (UX-5) already enforces the per-tenant cap
and name-uniqueness at storage time — this module's only job is to reject a
`ScreenDefinition` that cannot even compile (same `build_query_plan` +
`validate_plan_supported` pass `run_screen.py` runs before executing a
screen) *before* it is persisted, so a saved screen is never silently
un-runnable later.
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.screener.contracts.v1 import SavedScreenerView, ScreenDefinition
from src.foundation.screener.domain.evaluate import validate_plan_supported
from src.foundation.screener.domain.query_plan import build_query_plan
from src.foundation.screener.ports.repository import SavedScreenerRepository

__all__ = ["save_screen"]


async def save_screen(
    tenant_id: UUID,
    *,
    name: str,
    definition: ScreenDefinition,
    repo: SavedScreenerRepository,
) -> SavedScreenerView:
    """Raises `ScreenerConditionError` (domain/query_plan.py,
    domain/evaluate.py) if `definition` does not compile, or
    `SavedScreenerLimitError`/`SavedScreenerNameConflictError`
    (ports/repository.py) from the storage layer."""
    plan = build_query_plan(definition)
    validate_plan_supported(plan)
    return await repo.save(tenant_id=tenant_id, name=name, definition=definition)
