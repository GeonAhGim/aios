"""R-55 -- risk limit breach alerting with 5-minute duplicate suppression.

Spec: docs/specs/L4_risk_and_safety_v1.0.md, section 2 row 134, section 5(g),
section 9 R-55 (depends on R-25 = task-1316 risk_decision_recorder.py).

This does not add a new notification channel or gateway (section C bans
duplicate contexts) -- dispatch always goes through the existing
src/core/notifications/gateway.py interface (`handle_event`). The actual
forced-channel mapping for CRITICAL/WARN is FD-17 follow-up work; this
service only attaches a `severity` field to the payload it hands off.

Clock and gateway are both injected (I-0x: no global clock/singleton in
src/) so suppression-window tests do not depend on wall-clock time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from src.foundation.risk_gate.domain.models import LimitBreachSeverity

logger = logging.getLogger(__name__)

_SUPPRESSION_WINDOW = timedelta(minutes=5)


class AlertGateway(Protocol):
    """Matches `NotificationGateway.handle_event` -- the only method this
    service calls. A real gateway or a test fake both satisfy this."""

    async def handle_event(self, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class LimitBreachEvent:
    """One risk-limit breach, already classified hard/soft by the caller
    (e.g. from a `RiskLimit.hard` lookup or a `RISK_LIMIT_BREACH:<scope>:
    <metric>` reason code). `limit_id` is an opaque identifier for the
    breached limit (e.g. `RiskLimit.id` or `f"{scope}:{scope_ref}:{metric}"`)
    -- this service never inspects its structure, only uses it as a
    suppression-key component."""

    tenant_id: UUID
    user_id: UUID
    limit_id: str
    hard: bool


class RiskAlertService:
    def __init__(
        self,
        gateway: AlertGateway,
        clock: Callable[[], datetime],
        *,
        suppression_window: timedelta = _SUPPRESSION_WINDOW,
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._window = suppression_window
        # (tenant_id, limit_id, severity) -> last time a send was reserved.
        self._last_sent: dict[tuple[UUID, str, str], datetime] = {}
        # Guards the check-then-reserve step in on_breach() so concurrent
        # calls for the same key cannot both observe "not yet sent" before
        # either has recorded its reservation -- without this, two racing
        # coroutines could each dispatch inside the same 5-minute window.
        self._lock = asyncio.Lock()

    async def on_breach(self, event: LimitBreachEvent) -> None:
        """Fail-closed towards the caller: a gateway failure is logged and
        swallowed here so the decision-recording path that triggered this
        alert is never broken by a notification outage. A failed send is
        not cached, so the very next breach retries immediately instead of
        being suppressed by its own failed attempt."""
        severity = LimitBreachSeverity.CRITICAL if event.hard else LimitBreachSeverity.WARN
        key = (event.tenant_id, event.limit_id, severity.value)

        async with self._lock:
            now = self._clock()
            last = self._last_sent.get(key)
            if last is not None and now - last < self._window:
                return
            # Reserve the slot before the (possibly slow) gateway call so a
            # concurrent on_breach() for the same key sees this reservation
            # instead of racing to send a duplicate.
            self._last_sent[key] = now

        payload = {
            "event_type": "alert.triggered",
            "user_id": str(event.user_id),
            "tenant_id": str(event.tenant_id),
            "limit_id": event.limit_id,
            "severity": severity.value,
        }
        try:
            await self._gateway.handle_event(payload)
        except Exception:
            logger.exception(
                "risk_alert_dispatch_failed tenant_id=%s limit_id=%s severity=%s",
                event.tenant_id,
                event.limit_id,
                severity.value,
            )
            async with self._lock:
                if self._last_sent.get(key) == now:
                    del self._last_sent[key]
            return


__all__ = ["RiskAlertService", "LimitBreachEvent", "AlertGateway"]
