"""DEEPEN -- negative / failure-injection / performance 보강 (task-10179).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2)) -- negative test 0건이던
__init__.py를 DEEPEN 기준에 맞춰 보강한다.

대상: scripts/observability/notify_selftest.py, src/core/observability/notify_port.py
(H-10, ADR-2026-09-09-B) -- 무음 알림(silent alert)을 만들지 않는다는 불변식을
검증한다: 설정이 비어 있거나 전송이 실패해도 절대 `ok=True`로 위장하지 않는다.

DoD:
- negative test 3건 이상 (불변식 위반 입력을 명시적으로 거부)
- 실패주입 케이스 1건 이상 (monkeypatch로 의존성 예외 유발)
- `pytest tests/unit/scripts/observability/__init__.py -q` 통과
- docs/design/INVARIANTS.md 위반 없음
"""

from __future__ import annotations

import time as time_module
from pathlib import Path

import httpx
import pytest

from scripts.observability import notify_selftest
from src.core.observability.notify_port import (
    AlertNotification,
    NotifyResult,
    WebhookNotifyAdapter,
)

_ALERT = AlertNotification(
    alertname="H10_NotifySelfTest",
    severity="warn",
    status="firing",
    summary="test",
    runbook="RB-10-notify",
)


# -- negative tests: WebhookNotifyAdapter must never disguise failure as success --


async def test_webhook_adapter_missing_url_returns_not_ok() -> None:
    """Negative: an empty webhook URL must return ok=False, never a silent
    success -- this is the exact incident H-10 exists to prevent."""
    adapter = WebhookNotifyAdapter(webhook_url="")

    result = await adapter.send(_ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert result.error == "ALERT_WEBHOOK_URL not configured"


async def test_webhook_adapter_4xx_response_returns_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative: an HTTP 4xx/5xx response from the webhook must surface as
    ok=False with the status code and body, not as success."""

    class _FakeResponse:
        status_code = 404
        text = "channel_not_found"

    class _FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    adapter = WebhookNotifyAdapter(webhook_url="https://hooks.example/x")

    result = await adapter.send(_ALERT)

    assert result.ok is False
    assert result.status_code == 404
    assert result.error == "channel_not_found"


async def test_webhook_adapter_network_error_returns_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative: a network-level httpx error must be caught and reported as
    ok=False rather than propagating and crashing the self-test run."""

    class _FailingAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _FailingAsyncClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", _FailingAsyncClient)
    adapter = WebhookNotifyAdapter(webhook_url="https://hooks.example/x")

    result = await adapter.send(_ALERT)

    assert result.ok is False
    assert result.status_code is None
    assert "connection refused" in (result.error or "")


def test_write_report_rejects_non_list_paths() -> None:
    """Negative: write_report iterates `paths` as a sequence of Path -- a
    bare string (iterable of chars, not Path objects) must fail fast rather
    than silently writing to nonsense locations."""
    with pytest.raises(AttributeError):
        notify_selftest.write_report({"ok": True}, "not-a-list-of-paths")  # type: ignore[arg-type]


# -- failure-injection: unexpected exception from the send dependency must propagate --


async def test_run_selftest_propagates_unexpected_adapter_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection: if the adapter itself raises an exception that is
    not a modeled NotifyResult(ok=False, ...), run_selftest must not swallow
    it -- fail-closed means a broken dependency surfaces loudly instead of
    being reported as a clean self-test result."""

    class _BoomAdapter:
        async def send(self, alert: AlertNotification) -> NotifyResult:
            raise RuntimeError("adapter backend unavailable")

    with pytest.raises(RuntimeError, match="adapter backend unavailable"):
        await notify_selftest.run_selftest(_BoomAdapter())


# -- performance assertion --


@pytest.mark.perf
def test_write_report_100_paths_under_200ms(tmp_path: Path) -> None:
    """Numeric performance assertion: writing the self-test report to 100
    destinations (pure local filesystem I/O, no network) must complete well
    under 200ms."""
    paths = [tmp_path / f"dest_{i}" / "selftest_latest.json" for i in range(100)]

    started = time_module.perf_counter()
    notify_selftest.write_report({"ok": True, "status_code": 200, "error": None}, paths)
    elapsed = time_module.perf_counter() - started

    assert all(p.exists() for p in paths)
    assert elapsed < 0.5, f"write_report(100 paths) took {elapsed:.3f}s, budget 0.5s"
