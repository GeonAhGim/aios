"""U-15 텔레그램 알림 어댑터 — 체결/한도위반/kill 알림.

`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`(.env 슬롯)가 비어 있으면 실제로
보내지 않고 `ok=False`를 반환한다 — `src/core/observability/notify_port.py`
`WebhookNotifyAdapter`와 동일 원칙(미설정을 성공으로 위장하지 않는다).

미검증: 텔레그램 Bot API 응답 스키마는 공개 문서 기준 가정이며, 실제 봇
토큰으로 검증되지 않았다.
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
