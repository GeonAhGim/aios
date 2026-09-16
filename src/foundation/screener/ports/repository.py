"""Saved-screener storage port (Protocol) — U-1a.

domain/query_plan.py does not know about this port (pure, no I/O). Only the
application/adapters layers implement and consume this contract (same
convention as `execution_ownership/ports/repository.py`, 71 §4).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.screener.contracts.v1 import SavedScreenerView, ScreenDefinition


class SavedScreenerLimitError(Exception):
    """Per-tenant saved-screener cap (`MAX_SAVED_SCREENERS_PER_TENANT`, 50) exceeded."""


class SavedScreenerNameConflictError(Exception):
    """A saved screener with this name already exists for the tenant."""


class SavedScreenerRepository(Protocol):
    async def save(
        self, *, tenant_id: UUID, name: str, definition: ScreenDefinition
    ) -> SavedScreenerView:
        """Create a new saved screener.

        Raises `SavedScreenerLimitError` if the tenant is at the cap, or
        `SavedScreenerNameConflictError` if the name is already used within
        the tenant.
        """
        ...

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[SavedScreenerView, ...]: ...

    async def get(self, tenant_id: UUID, screener_id: UUID) -> SavedScreenerView | None:
        """Cross-tenant reads return `None` (caller maps it to 404) — even if
        the row exists, a mismatched `tenant_id` never reveals that fact."""
        ...

    async def delete(self, tenant_id: UUID, screener_id: UUID) -> bool:
        """`True` if a row was deleted. A cross-tenant delete attempt
        silently returns `False` (indistinguishable from "no such row" —
        existence is never revealed)."""
        ...
