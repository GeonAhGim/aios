"""Webhook notification port + Slack adapter.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-10.
Even after the 11 rules in config/observability/alert_rules.yaml are routed through
config/observability/alertmanager.yml, nothing reaches a human unless something actually
sends. `NotifyPort` is a pure interface exposing the single operation send(alert), and
`WebhookNotifyAdapter` delivers a Slack Incoming Webhook compatible payload
(`{"text": ...}`) -- one adapter for now, PagerDuty can be added later on the same port.

When `ALERT_WEBHOOK_URL` (.env slot) is empty, `WebhookNotifyAdapter.send` returns
`ok=False` instead of quietly pretending success -- the whole point of H-10 is to remove
"silent alerts", so disguising a missing configuration as success would reproduce exactly
that incident.
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

    When `webhook_url` is omitted, `ALERT_WEBHOOK_URL` (.env) is read. If the value is
    empty, nothing is sent and `ok=False` is returned (a missing configuration is never
    disguised as success).
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
