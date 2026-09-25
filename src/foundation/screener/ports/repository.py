"""Saved-screener storage port (Protocol) — U-1a.

domain/query_plan.py does not know about this port (pure, no I/O). Only the
application/adapters layers implement and consume this contract (same
convention as `execution_ownership/ports/repository.py`, 71 §4).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.screener.contracts.v1 import (
    SavedScreenerView,
    ScreenAlertView,
    ScreenDefinition,
    SharedScreenerView,
)


class SavedScreenerLimitError(Exception):
    """Per-tenant saved-screener cap (`MAX_SAVED_SCREENERS_PER_TENANT`, 50) exceeded."""


class SavedScreenerNameConflictError(Exception):
    """A saved screener with this name already exists for the tenant."""


class ScreenAlertLimitError(Exception):
    """Per-tenant active screen-alert cap (`MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT`) exceeded."""


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


class SharedScreenerRepository(Protocol):
    """Storage port for `shared_screeners` — cross-tenant readable by design
    (that is the point of "share"), writes restricted to the owning tenant."""

    async def create_version(
        self, *, tenant_id: UUID, screener_id: UUID, name: str, definition: ScreenDefinition
    ) -> SharedScreenerView:
        """Insert the next immutable version for `screener_id` (prior max + 1,
        starting at 1). Never mutates an existing row — sharing again after
        editing the source screener creates a new version, past versions
        stay frozen."""
        ...

    async def get_latest(self, screener_id: UUID) -> SharedScreenerView | None:
        """`None` only when `screener_id` was never shared."""
        ...

    async def list_versions(self, screener_id: UUID) -> tuple[SharedScreenerView, ...]: ...


class ScreenAlertRepository(Protocol):
    async def create(
        self, *, tenant_id: UUID, screener_id: UUID, operator: str, threshold: int
    ) -> ScreenAlertView:
        """Raises `ScreenAlertLimitError` if the tenant is already at
        `MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT` ACTIVE alerts."""
        ...

    async def list_for_tenant(self, tenant_id: UUID) -> tuple[ScreenAlertView, ...]: ...

    async def get(self, tenant_id: UUID, alert_id: UUID) -> ScreenAlertView | None:
        """Cross-tenant reads return `None`, same convention as
        `SavedScreenerRepository.get`."""
        ...

    async def cancel(self, tenant_id: UUID, alert_id: UUID) -> bool:
        """`True` only if an ACTIVE row owned by `tenant_id` was cancelled."""
        ...

    async def mark_triggered(self, alert_id: UUID, *, matched_count: int) -> ScreenAlertView | None:
        """Idempotent: only an ACTIVE row transitions to TRIGGERED — calling
        this again on an already-TRIGGERED/CANCELLED row is a no-op (`None`)."""
        ...

    async def list_active(self) -> tuple[ScreenAlertView, ...]:
        """All ACTIVE alerts across every tenant — the evaluation loop's
        input, same convention as `AlertService.evaluate_all_active`
        (`src/services/alert_service.py`)."""
        ...
