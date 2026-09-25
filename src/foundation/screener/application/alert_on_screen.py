"""UX-7 — conditional alert on a saved screen's matched-row count.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2
`application/{save_screen,share_screen,alert_on_screen}.py` (conditional alert).

Mirrors `price_alerts`(FD-14, `src/services/alert_service.py`)'s
create/list/cancel/evaluate shape, but the condition compared is the
screen's matched-row count (`application/run_screen.py::ScreenRunPage.total`)
against `threshold` by `operator`, evaluated by re-running the screen — not
a single indicator value read off one symbol's candles. Like
`AlertService.evaluate_all_active`, `evaluate_screen_alerts` only proves a
trigger and flips `status` to TRIGGERED; there is still no background
scheduler in this codebase (same honest gap `alert_service.py`'s module
docstring documents) — a caller wires this into `background_loops.py`'s
periodic-loop convention as a follow-up leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.foundation.screener.application.run_screen import (
    DEFAULT_PAGE_SIZE,
    ScreenCursorError,
    ScreenLimitExceededError,
    ScreenResultCache,
    ScreenTimeoutError,
    ScreenUniverseError,
    run_saved_screen,
)
from src.foundation.screener.contracts.v1 import ScreenAlertOperator, ScreenAlertView
from src.foundation.screener.domain.evaluate import ScreenerEvaluationError
from src.foundation.screener.ports.field_source import ScreenerFieldSource
from src.foundation.screener.ports.repository import SavedScreenerRepository, ScreenAlertRepository

_SCREEN_EXECUTION_ERRORS = (
    ScreenCursorError,
    ScreenLimitExceededError,
    ScreenTimeoutError,
    ScreenUniverseError,
    ScreenerEvaluationError,
)

__all__ = [
    "ScreenAlertTrigger",
    "cancel_screen_alert",
    "create_screen_alert",
    "evaluate_screen_alerts",
    "list_screen_alerts",
]

_OPERATORS = {
    "gte": lambda matched, threshold: matched >= threshold,
    "gt": lambda matched, threshold: matched > threshold,
    "lte": lambda matched, threshold: matched <= threshold,
    "lt": lambda matched, threshold: matched < threshold,
    "eq": lambda matched, threshold: matched == threshold,
}


@dataclass(frozen=True, slots=True)
class ScreenAlertTrigger:
    alert: ScreenAlertView
    matched_count: int


async def create_screen_alert(
    tenant_id: UUID,
    screener_id: UUID,
    *,
    operator: ScreenAlertOperator,
    threshold: int,
    saved_repo: SavedScreenerRepository,
    alert_repo: ScreenAlertRepository,
) -> ScreenAlertView | None:
    """`None` means "no such saved screener for this tenant" (cross-tenant
    attempt included) — same convention as `share_screen.share_screen`.
    Raises `ScreenAlertLimitError` (ports/repository.py) at the per-tenant
    active-alert cap."""
    saved = await saved_repo.get(tenant_id, screener_id)
    if saved is None:
        return None
    return await alert_repo.create(
        tenant_id=tenant_id, screener_id=saved.id, operator=operator, threshold=threshold
    )


async def list_screen_alerts(
    tenant_id: UUID, *, alert_repo: ScreenAlertRepository
) -> tuple[ScreenAlertView, ...]:
    return await alert_repo.list_for_tenant(tenant_id)


async def cancel_screen_alert(
    tenant_id: UUID, alert_id: UUID, *, alert_repo: ScreenAlertRepository
) -> bool:
    """`True` only if an ACTIVE alert owned by `tenant_id` was cancelled —
    same convention as `SavedScreenerRepository.delete`."""
    return await alert_repo.cancel(tenant_id, alert_id)


async def evaluate_screen_alerts(
    *,
    alert_repo: ScreenAlertRepository,
    saved_repo: SavedScreenerRepository,
    field_source: ScreenerFieldSource,
    cache: ScreenResultCache,
) -> list[ScreenAlertTrigger]:
    """Runs every ACTIVE alert's screen and flips triggered ones to
    TRIGGERED. A screener deleted out from under an alert (`saved_repo.get`
    returns `None`) or a screen execution error (timeout, unknown universe,
    ...) is skipped this cycle, not raised — mirrors `alert_service.py`'s
    "one alert's failure never blocks the rest of the evaluation cycle"
    invariant (same fail-open-per-item shape, `evaluate_all_active`'s
    per-credential try/except)."""
    triggers: list[ScreenAlertTrigger] = []
    for alert in await alert_repo.list_active():
        saved = await saved_repo.get(alert.tenant_id, alert.screener_id)
        if saved is None:
            continue
        try:
            page = await run_saved_screen(
                alert.tenant_id,
                alert.screener_id,
                repo=saved_repo,
                field_source=field_source,
                cache=cache,
                page_size=DEFAULT_PAGE_SIZE,
            )
        except _SCREEN_EXECUTION_ERRORS:
            continue
        if page is None:
            continue
        if not _OPERATORS[alert.operator](page.total, alert.threshold):
            continue
        triggered = await alert_repo.mark_triggered(alert.id, matched_count=page.total)
        if triggered is not None:
            triggers.append(ScreenAlertTrigger(alert=triggered, matched_count=page.total))
    return triggers
