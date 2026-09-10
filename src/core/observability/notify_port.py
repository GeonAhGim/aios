"""웹훅 알림 포트 + Slack 어댑터.

Spec: docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md H-10.
config/observability/alert_rules.yaml 11개 규칙이 config/observability/alertmanager.yml의
라우팅을 거쳐도, 실제로 무언가가 발신하지 않으면 사람에게 닿지 않는다. `NotifyPort`는
send(alert) 한 연산만 노출하는 순수 인터페이스이고, `WebhookNotifyAdapter`가 Slack
Incoming Webhook 호환 페이로드(`{"text": ...}`)로 발신한다(어댑터 1종 — PagerDuty는
같은 포트 위에 추후 추가 가능).

`ALERT_WEBHOOK_URL`(.env 슬롯)이 비어 있으면 `WebhookNotifyAdapter.send`는 조용히
성공한 척하지 않고 `ok=False`를 돌려준다 — H-10의 목적 자체가 "무음 알림"을 없애는
것이므로, 미설정을 성공으로 위장하면 그 사고를 그대로 재현하게 된다.
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
    """Slack Incoming Webhook 호환 발신 어댑터.

    `webhook_url`을 생략하면 `ALERT_WEBHOOK_URL`(.env)을 읽는다. 값이 비어 있으면
    실제로 발신하지 않고 `ok=False`를 돌려준다(미설정을 성공으로 위장하지 않는다).
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
