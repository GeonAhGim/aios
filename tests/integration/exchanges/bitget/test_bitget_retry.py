"""6.11 — BitgetAdapter HTTP retry/error-classification integration tests.

Split out of the former single `tests/integration/test_bitget_adapter.py`
(788 lines, task-4225) — this file covers FULL_AUDIT §2-B ① (HTTP status
code handling, retry backoff, server-time offset sync) plus the generic
error-code-to-exception mapping (retryable vs fatal).
"""
from decimal import Decimal

import httpx
import pytest

from src.core.exceptions import FatalExchangeError, RetryableExchangeError


async def test_api_error_response_raises_retryable_by_default(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"code": "99999", "msg": "internal error", "data": {}})

    adapter = make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_signature_error_code_raises_fatal(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"code": "40012", "msg": "invalid sign", "data": {}})

    adapter = make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTC/USDT")


# ---------- FULL_AUDIT §2-B ① — HTTP 상태코드/재시도/서버시간 오프셋 ----------


async def test_request_retries_429_then_succeeds(make_adapter, json_response, real_ticker_envelope):
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, text="rate limited")
        return json_response(real_ticker_envelope)

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    ticker = await adapter.get_ticker("BTC/USDT")

    assert ticker.price == Decimal("80663.08")
    assert calls["n"] == 3
    assert sleep_calls == [1.0, 2.0]  # 지수 백오프


async def test_request_respects_retry_after_header(
    make_adapter, json_response, real_ticker_envelope
):
    calls = {"n": 0}
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, text="rate limited")
        return json_response(real_ticker_envelope)

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    await adapter.get_ticker("BTC/USDT")

    assert sleep_calls == [5.0]


async def test_request_retries_5xx_then_raises_retryable_after_exhausting(make_adapter):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async def fake_sleep(seconds: float) -> None:
        return None

    adapter = make_adapter(handler, sleep_fn=fake_sleep)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_request_raises_fatal_immediately_on_other_4xx_without_retry(make_adapter):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    adapter = make_adapter(handler)
    with pytest.raises(FatalExchangeError):
        await adapter.get_ticker("BTC/USDT")
    assert calls["n"] == 1  # 재시도 없이 즉시 실패


async def test_request_raises_retryable_on_non_json_response(make_adapter):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    adapter = make_adapter(handler)
    with pytest.raises(RetryableExchangeError):
        await adapter.get_ticker("BTC/USDT")


async def test_sync_server_time_updates_offset(make_adapter, json_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/public/time"
        return json_response(
            {"code": "00000", "msg": "success", "data": {"serverTime": "9999999999999"}}
        )

    adapter = make_adapter(handler)
    await adapter.sync_server_time()

    assert adapter._time_offset_ms != 0


async def test_sync_server_time_falls_back_to_zero_offset_on_failure(make_adapter):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    adapter = make_adapter(handler)
    await adapter.sync_server_time()

    assert adapter._time_offset_ms == 0
