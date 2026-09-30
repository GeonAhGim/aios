"""NotifyPort/WebhookNotifyAdapter 단위테스트 -- H-10(task-2614, ADR-2026-09-09-B).

DoD "웹훅 수신 mock으로 전달 테스트": httpx.MockTransport로 웹훅 수신 서버를 모킹해
페이로드(text 필드에 alertname/severity/summary/runbook 포함)와 대상 URL이 기대와
일치하는지, 미설정/4xx/5xx/네트워크 예외를 각각 ok=False로 구분하는지 검증한다.
"""

from __future__ import annotations

import time

import httpx
import pytest

from src.core.observability.notify_port import AlertNotification, NotifyResult, WebhookNotifyAdapter

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


async def test_unset_env_and_omitted_url_falls_back_to_not_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    """webhook_url을 생략하고 ALERT_WEBHOOK_URL 환경변수도 없는 경우 -- 생성자가 빈 문자열로
    폴백해 미설정으로 취급해야 한다(H-10은 설정 누락을 성공으로 위장하지 않는다)."""
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    adapter = WebhookNotifyAdapter()

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert result.error == "ALERT_WEBHOOK_URL not configured"


async def test_send_server_error_status_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service_unavailable")

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code == 503
    assert "service_unavailable" in (result.error or "")


async def test_notify_result_is_immutable_against_post_hoc_ok_override():
    """NotifyResult는 frozen dataclass -- 수신측이 결과를 받은 뒤 ok 필드를 임의로
    True로 덮어써 실패를 성공으로 위장할 수 없어야 한다(H-10 불변식)."""
    result = NotifyResult(ok=False, status_code=500, error="boom")

    with pytest.raises(AttributeError):
        result.ok = True  # type: ignore[misc]


async def test_send_timeout_exception_is_not_ok(monkeypatch: pytest.MonkeyPatch):
    """실패주입: transport가 httpx.TimeoutException을 던지는 경우도 httpx.HTTPError의
    서브클래스이므로 ok=False로 구분되어야 한다(ConnectError 외 다른 네트워크 예외 경로)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    result = await adapter.send(ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert "timed out" in (result.error or "")


async def test_send_success_latency_stays_under_budget(monkeypatch: pytest.MonkeyPatch):
    """성능 단언: 로컬 MockTransport 왕복은 네트워크 I/O가 없으므로 500ms 예산 내에
    끝나야 한다 -- 어댑터가 불필요한 재시도/블로킹 대기를 추가하지 않았는지 감시한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _mock_client(monkeypatch, httpx.MockTransport(handler))
    adapter = WebhookNotifyAdapter(WEBHOOK_URL)

    start = time.monotonic()
    result = await adapter.send(ALERT)
    elapsed = time.monotonic() - start

    assert result.ok is True
    assert elapsed < 0.5
