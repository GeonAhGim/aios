"""BR-10(ADR-2026-09-06-I D6) — `common/oauth_http.py` 단위 테스트.

KIS/NH가 공유하는 두 책임(monotonic 토큰 캐시, 전송 오류 변환)을 각
어댑터의 네트워크 계층 없이 순수하게 검증한다.
"""
from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.exchanges.common import oauth_http
from src.exchanges.common.oauth_http import MonotonicTokenCache, send_or_raise_retryable


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_token_cache_returns_none_when_empty() -> None:
    cache = MonotonicTokenCache()
    assert cache.get() is None


def test_token_cache_returns_cached_token_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock(100.0)
    monkeypatch.setattr(oauth_http.time, "monotonic", clock)
    cache = MonotonicTokenCache()

    cache.set("tok-1", ttl_seconds=60.0)
    clock.now += 30.0

    assert cache.get() == "tok-1"


def test_token_cache_expires_after_ttl_elapses(monkeypatch: pytest.MonkeyPatch) -> None:
    """음수 회귀 방지 — TTL을 넘기면 만료된 토큰을 조용히 재사용하지 않는다."""
    clock = _FakeClock(100.0)
    monkeypatch.setattr(oauth_http.time, "monotonic", clock)
    cache = MonotonicTokenCache()

    cache.set("tok-1", ttl_seconds=60.0)
    clock.now += 61.0

    assert cache.get() is None


def test_token_cache_set_clamps_negative_ttl_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock(100.0)
    monkeypatch.setattr(oauth_http.time, "monotonic", clock)
    cache = MonotonicTokenCache()

    cache.set("tok-1", ttl_seconds=-5.0)

    assert cache.get() is None


async def test_send_or_raise_retryable_returns_response_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    response = await send_or_raise_retryable(
        client, "POST", "/x", venue="TEST", body={}, headers={}
    )
    assert response.json() == {"ok": True}
    await client.aclose()


async def test_send_or_raise_retryable_wraps_transport_error() -> None:
    """네트워크 계층 실패(연결 거부 등)는 재시도 가능 오류로 변환돼야
    호출부(outbox 재시도 경로)가 이를 영구 실패와 구분할 수 있다."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = httpx.AsyncClient(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(RetryableExchangeError):
        await send_or_raise_retryable(client, "POST", "/x", venue="TEST", body={}, headers={})
    await client.aclose()
