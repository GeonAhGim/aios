"""NotifyPort/WebhookNotifyAdapter 단위테스트 -- H-10(task-2614, ADR-2026-09-09-B).

DoD "웹훅 수신 mock으로 전달 테스트": httpx.MockTransport로 웹훅 수신 서버를 모킹해
페이로드(text 필드에 alertname/severity/summary/runbook 포함)와 대상 URL이 기대와
일치하는지, 미설정/4xx/5xx/네트워크 예외를 각각 ok=False로 구분하는지 검증한다.
"""

from __future__ import annotations

import httpx
import pytest

from src.core.observability.notify_port import AlertNotification, WebhookNotifyAdapter

ALERT = AlertNotification(
    alertname="A4_LiveBlockedInPaper",
    severity="critical",
    status="firing",
    summary="PAPER 런타임에서 LIVE 주문 시도가 차단됨",
    runbook="RB-03",
    labels={"mode": "live_blocked"},
)

WEBHOOK_URL = "https://hooks.slack.example/services/T00/B00/xxx"


def _mock_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    import src.core.observability.notify_port as notify_port_module

    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(notify_port_module.httpx, "AsyncClient", _factory)


async def test_missing_webhook_url_returns_not_configured():
    adapter = WebhookNotifyAdapter("")

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert result.error == "ALERT_WEBHOOK_URL not configured"


async def test_send_success_delivers_expected_payload_to_configured_url(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    result = await adapter.send(ALERT)

    assert result.ok is True
    assert result.status_code == 200
    assert result.error is None
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url) == WEBHOOK_URL
    body = req.content.decode("utf-8")
    assert "A4_LiveBlockedInPaper" in body
    assert "critical" in body
    assert "PAPER 런타임에서 LIVE 주문 시도가 차단됨" in body
    assert "RB-03" in body


async def test_send_http_error_status_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="channel_not_found")

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code == 404
    assert "channel_not_found" in (result.error or "")


async def test_send_network_error_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert result.error is not None
