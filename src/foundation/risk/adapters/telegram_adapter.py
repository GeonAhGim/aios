"""U-15 Telegram notifier adapter — fill/limit-breach/kill alerts.

If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (.env slots) are empty, this
does not actually send and returns `ok=False` — same principle as
`WebhookNotifyAdapter` in `src/core/observability/notify_port.py` (a
missing config is never disguised as success).

Unverified: the Telegram Bot API response shape is assumed from public
docs and has not been verified against a real bot token.
"""

from __future__ import annotations

import os

import httpx

from src.foundation.risk.ports.notifier import NotifyResult, PersonalNotification

_API_BASE = "https://api.telegram.org"


class TelegramNotifierAdapter:
    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._bot_token = (
            bot_token if bot_token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        )
        self._chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self._timeout = timeout

    async def send(self, notification: PersonalNotification) -> NotifyResult:
        if not self._bot_token or not self._chat_id:
            return NotifyResult(ok=False, status_code=None, error="telegram not configured")
        url = f"{_API_BASE}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": f"[{notification.kind.value}] {notification.message}",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload)
        except httpx.HTTPError as e:
            return NotifyResult(ok=False, status_code=None, error=str(e))
        if resp.status_code >= 400:
            return NotifyResult(ok=False, status_code=resp.status_code, error=resp.text[:300])
        return NotifyResult(ok=True, status_code=resp.status_code, error=None)
