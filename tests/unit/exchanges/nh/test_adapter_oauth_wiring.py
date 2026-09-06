"""BR-10(ADR-2026-09-06-I D6) — NHAdapter가 common/oauth_http.py를 실제로
쓰는지(배선) 증명한다.

`tests/integration/test_nh_adapter.py`는 `_ensure_token`/`_request`의
업무 동작(엔드포인트·필드명)을 이미 검증한다 — 이 파일은 그와 별개로
"이 리프에서 새로 올린 공통 로직이 실제로 호출 경로에 배선됐는가"만
좁게 확인한다(negative: 배선이 빠지면 전송 오류가 조용히 다른 예외로
새거나 그대로 전파돼야 함).
"""
from __future__ import annotations

import httpx
import pytest

from src.core.exceptions import RetryableExchangeError
from src.exchanges.common.oauth_http import MonotonicTokenCache
from src.exchanges.nh.adapter import NHAdapter

TOKEN_RESPONSE = {"access_token": "tok-1", "expires_in": 86400}


def _make_adapter(handler) -> NHAdapter:
    client = httpx.AsyncClient(
        base_url="https://moapi.nhplug.com:8443", transport=httpx.MockTransport(handler)
    )
    return NHAdapter("appkey", "appsecret", "1234567890", http_client=client)


def test_nh_adapter_uses_monotonic_token_cache() -> None:
    adapter = _make_adapter(lambda request: httpx.Response(200, json=TOKEN_RESPONSE))
    assert isinstance(adapter._token_cache, MonotonicTokenCache)


async def test_nh_request_transport_error_is_retryable() -> None:
    """`_request()`가 여전히 `send_or_raise_retryable()`을 거쳐 전송하는지 —
    연결 실패가 무음으로 삼켜지거나 원시 `httpx.TransportError`로 그대로
    새면 outbox 재시도 분류(§5.4)가 깨진다."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        raise httpx.ConnectError("boom", request=request)

    adapter = _make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("005930")
    assert call_count["n"] >= 1
