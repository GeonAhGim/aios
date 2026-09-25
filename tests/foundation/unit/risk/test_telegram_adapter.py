"""U-15 텔레그램 어댑터 단위테스트.

DoD "텔레그램 mock 전달 테스트": httpx.MockTransport로 텔레그램 Bot API를
모킹해 페이로드(chat_id/text)가 기대와 일치하는지, 미설정/4xx/네트워크
예외를 각각 ok=False로 구분하는지 검증한다
(tests/unit/core/observability/test_notify_port.py와 동일 패턴).
"""

from __future__ import annotations

import httpx
import pytest

from src.foundation.risk.adapters.telegram_adapter import TelegramNotifierAdapter
from src.foundation.risk.ports.notifier import PersonalNotification, PersonalNotificationKind

NOTIFICATION = PersonalNotification(
    kind=PersonalNotificationKind.KILL_SWITCH,
    message="일일 손실 한도 초과로 자동 kill 발동",
)


def _mock_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    import src.foundation.risk.adapters.telegram_adapter as telegram_adapter_module

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(telegram_adapter_module.httpx, "AsyncClient", _factory)


async def test_missing_credentials_returns_not_configured():
    adapter = TelegramNotifierAdapter(bot_token="", chat_id="")

    result = await adapter.send(NOTIFICATION)

    assert result.ok is False
    assert result.status_code is None
    assert result.error == "telegram not configured"


async def test_missing_chat_id_only_still_not_configured():
    adapter = TelegramNotifierAdapter(bot_token="123:abc", chat_id="")

    result = await adapter.send(NOTIFICATION)

    assert result.ok is False


async def test_send_success_delivers_expected_payload(monkeypatch: pytest.MonkeyPatch):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = TelegramNotifierAdapter(bot_token="123:abc", chat_id="999")

    result = await adapter.send(NOTIFICATION)

    assert result.ok is True
    assert result.status_code == 200
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == "https://api.telegram.org/bot123:abc/sendMessage"
    body = req.content.decode("utf-8")
    assert '"chat_id"' in body and '"999"' in body
    assert "KILL_SWITCH" in body
    assert "일일 손실 한도 초과" in body


async def test_send_http_error_status_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Request: chat not found")

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = TelegramNotifierAdapter(bot_token="123:abc", chat_id="bad-chat")

    result = await adapter.send(NOTIFICATION)

    assert result.ok is False
    assert result.status_code == 400
    assert "chat not found" in (result.error or "")


async def test_send_network_error_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = TelegramNotifierAdapter(bot_token="123:abc", chat_id="999")

    result = await adapter.send(NOTIFICATION)

    assert result.ok is False
    assert result.status_code is None
    assert result.error is not None
