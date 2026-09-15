"""Webhook alert port + Slack adapter.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-10.
Even if config/observability/alert_rules.yaml's 11 rules route through
config/observability/alertmanager.yml, nothing reaches a human unless
something actually sends. `NotifyPort` is a pure interface exposing only
send(alert); `WebhookNotifyAdapter` sends a Slack Incoming Webhook compatible
payload (`{"text": ...}`) (one adapter for now -- PagerDuty can be added
later on the same port).

If `ALERT_WEBHOOK_URL` (.env slot) is empty, `WebhookNotifyAdapter.send`
does not silently pretend to succeed -- it returns `ok=False`. H-10 exists
specifically to eliminate "silent alerts", so treating a missing config as
success would reproduce that exact incident.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass(frozen=True)
class AlertNotification:
    alertname: str
    severity: str
    status: str  # "firing" | "resolved"
    summary: str
    runbook: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    status_code: int | None
    error: str | None


class NotifyPort(Protocol):
    async def send(self, alert: AlertNotification) -> NotifyResult: ...


def _format_slack_text(alert: AlertNotification) -> str:
    icon = "\U0001f534" if alert.severity == "critical" else "\U0001f7e1"
    state = "FIRING" if alert.status == "firing" else "RESOLVED"
    return (
        f"{icon} [{state}] {alert.alertname} ({alert.severity})\n"
        f"{alert.summary}\n"
        f"runbook: docs/runbooks/{alert.runbook}.md"
    )


class WebhookNotifyAdapter:
    """Slack Incoming Webhook compatible sender adapter.

    If `webhook_url` is omitted, reads `ALERT_WEBHOOK_URL` (.env). If that
    value is empty, does not actually send and returns `ok=False` (a missing
    config is never disguised as success).
    """

    def __init__(self, webhook_url: str | None = None, *, timeout: float = 10.0) -> None:
        self._webhook_url = (
            webhook_url if webhook_url is not None else os.environ.get("ALERT_WEBHOOK_URL", "")
        )
        self._timeout = timeout

    async def send(self, alert: AlertNotification) -> NotifyResult:
        if not self._webhook_url:
            return NotifyResult(
                ok=False, status_code=None, error="ALERT_WEBHOOK_URL not configured"
            )
        payload = {"text": _format_slack_text(alert)}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._webhook_url, json=payload)
        except httpx.HTTPError as e:
            return NotifyResult(ok=False, status_code=None, error=str(e))
        if resp.status_code >= 400:
            return NotifyResult(ok=False, status_code=resp.status_code, error=resp.text[:300])
        return NotifyResult(ok=True, status_code=resp.status_code, error=None)
