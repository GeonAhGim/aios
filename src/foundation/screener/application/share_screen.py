"""UX-7 — share a saved screen as an immutable, cross-tenant-readable version.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/{save_screen,share_screen,alert_on_screen}.py` (share, market
rules).

"Market rules" here is the MP-3 immutable-version invariant —
`marketplace/domain/versioning.py` is still `hold`
(L4_analytics_authoring_backtest_marketplace_v1.0.md §9.7), so this module
applies that invariant directly against `shared_screeners` instead of
importing it (see `contracts.v1.SharedScreenerView` docstring): only the
*owning* tenant may share (enforced by requiring `SavedScreenerRepository.get`
to resolve for that tenant first — cross-tenant share attempts get the same
`None` treatment as a cross-tenant read, §71 §4 convention), and every share
call appends a new version rather than mutating a previous one.
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.screener.contracts.v1 import SharedScreenerView
from src.foundation.screener.ports.repository import (
    SavedScreenerRepository,
    SharedScreenerRepository,
)

__all__ = ["share_screen"]


async def share_screen(
    tenant_id: UUID,
    screener_id: UUID,
    *,
    saved_repo: SavedScreenerRepository,
    shared_repo: SharedScreenerRepository,
) -> SharedScreenerView | None:
    """`None` means "no such saved screener for this tenant" (cross-tenant
    share attempt included) — the caller maps it to 404, it is never raised
    as an exception here (same convention as `run_screen.run_saved_screen`)."""
    saved = await saved_repo.get(tenant_id, screener_id)
    if saved is None:
        return None
    return await shared_repo.create_version(
        tenant_id=tenant_id,
        screener_id=saved.id,
        name=saved.name,
        definition=saved.definition,
    )
